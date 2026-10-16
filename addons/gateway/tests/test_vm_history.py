import json
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from gateway import config, deps, vm_history
from gateway.asof import iso
from gateway.glossary import store as glossary
from gateway.main import app
from gateway.metric_catalog import store as catalog
from gateway.metric_catalog.models import EntityRecord, SeriesRecord

T0 = 1_800_000_000.0 - 1_800_000_000.0 % 86400  # a UTC midnight
H = 3600
UTC = ZoneInfo("UTC")
TEMP_A = '{__name__="°F_value",entity_id="gh_temp",domain="sensor"}'
TEMP_B = '{__name__="°F_value",entity_id="gh_temp",domain="sensor",source="old"}'
FAN = '{__name__="value",entity_id="gaht",domain="switch"}'


def export_line(selector_name, samples):
    return json.dumps({"metric": {"__name__": selector_name}, "values": [v for _, v in samples],
                       "timestamps": [int(t * 1000) for t, _ in samples]})


class FakeVM:
    """Serves /api/v1/export from a dict keyed by selector, and records what was asked."""

    def __init__(self, data, status=200):
        self.data, self.status, self.requests = data, status, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        self.requests.append({"match": params.get_list("match[]"), "start": float(params["start"]),
                              "end": float(params["end"])})
        if self.status != 200:
            return httpx.Response(self.status, text="denied")
        start, end = float(params["start"]), float(params["end"])
        lines = []
        for sel in params.get_list("match[]"):
            rows = [(t, v) for t, v in self.data.get(sel, []) if start <= t <= end]
            if rows:
                lines.append(export_line(sel, rows))
        return httpx.Response(200, text="\n".join(lines))

    def client(self):
        return httpx.Client(base_url="http://vm", transport=httpx.MockTransport(self.handler))


class FakeHA:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def get(self, path, **params):
        self.calls.append((path, params))
        if path == "/config":
            return {"time_zone": "UTC"}
        return [[{"entity_id": eid, "state": s, "last_changed": iso(t)} for t, s in self.rows[eid]]
                for eid in params["filter_entity_id"].split(",") if eid in self.rows]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOSSARY_DB", str(tmp_path / "g.sqlite3"))
    ents = {"sensor.gh_temp": EntityRecord("sensor.gh_temp", friendly_name="GH Temp", unit="°F", vm_status="ok"),
            "switch.gaht": EntityRecord("switch.gaht", friendly_name="GAHT fan", vm_status="ok"),
            "sensor.ha_only": EntityRecord("sensor.ha_only", friendly_name="HA only", vm_status="missing")}
    sers = [SeriesRecord("sensor.gh_temp", "°F_value", {}, TEMP_A, field="value", kind="value"),
            SeriesRecord("sensor.gh_temp", "°F_value", {}, TEMP_B, field="value", kind="value"),
            SeriesRecord("switch.gaht", "value", {}, FAN, field="value", kind="value")]
    cat = catalog.connect(config.catalog_db())
    catalog.save_run(cat, "influxdb", ents, sers)
    cat.close()
    conn = glossary.connect(config.glossary_db())
    glossary.sync(catalog.connect(config.catalog_db()), conn, {}, now=T0)
    for eid, role in (("sensor.gh_temp", "greenhouse_temperature"), ("switch.gaht", "equipment_gaht_fan")):
        glossary.owner_update(conn, eid, "Sam", meaning=f"the {role}", aliases=[f"role:{role}"], status="approved")
    conn.close()
    vm = FakeVM({TEMP_A: [(T0 - 2 * H, 50.0), (T0, 60.0), (T0 + H, 70.0)],
                 TEMP_B: [(T0 + 2 * H, 80.0), (T0 + H, 70.0)],  # a handoff, with one shared timestamp
                 FAN: [(T0 - H, 0.0), (T0 + 2 * H, 1.0), (T0 + 5 * H, 0.0)]})
    ha = FakeHA({"sensor.ha_only": [(T0, "5"), (T0 + H, "7")]})
    app.dependency_overrides[deps.get_vm] = vm.client
    app.dependency_overrides[deps.get_ha] = lambda: ha
    yield TestClient(app), vm, ha
    app.dependency_overrides.clear()


def get(c, path, **params):
    return c.get(path, params=params)


def test_export_merges_series_and_drops_non_finite():
    class V(FakeVM):
        pass
    vm = V({"a": [(1.0, 5.0), (3.0, float("nan"))], "b": [(2.0, 6.0), (1.0, 9.0)]})
    points, n = vm_history.export(vm.client(), ["a", "b"], 0, 10)
    assert n == 2 and points == [(1.0, 9.0), (2.0, 6.0)]  # b's value wins at the shared timestamp; NaN dropped
    assert vm_history.export(vm.client(), [], 0, 10) == ([], 0)
    assert vm_history.on_off([(1, 0.0), (2, 0.5), (3, 1.0)]) == [(1, "off"), (2, "on"), (3, "on")]


def test_export_errors_are_named():
    with pytest.raises(vm_history.VMError, match="not authorised"):
        vm_history.export(FakeVM({}, status=401).client(), ["a"], 0, 1)
    with pytest.raises(vm_history.VMError, match="HTTP 500"):
        vm_history.export(FakeVM({}, status=500).client(), ["a"], 0, 1)


def test_resampled_reads_vm_and_merges_a_handoff(env):
    c, vm, _ = env
    r = get(c, "/history/resampled", entity_id="sensor.gh_temp", step="1h", start=iso(T0), end=iso(T0 + 4 * H))
    assert r.status_code == 200
    s = r.json()["series"][0]
    assert s["source"] == "vm" and s["series_merged"] == 2 and s["first_sample"] == T0
    assert [v for _, v in s["points"]] == [60.0, 70.0, 80.0, 80.0]
    assert sorted(vm.requests[0]["match"]) == sorted([TEMP_A, TEMP_B])
    assert vm.requests[0]["start"] == T0 - 86400  # a day of carry-in before the window


def test_the_reading_before_the_window_carries_in(env):
    c, _, _ = env
    r = get(c, "/history/resampled", entity_id="sensor.gh_temp", step="1h", start=iso(T0 - H), end=iso(T0 + H))
    assert [v for _, v in r.json()["series"][0]["points"]] == [50.0, 60.0]


def test_a_long_window_is_allowed_on_vm_but_not_on_ha(env):
    c, _, _ = env
    long_start = iso(T0 - 200 * 86400)
    assert get(c, "/history/resampled", entity_id="sensor.gh_temp", step="24h", start=long_start,
               end=iso(T0)).status_code == 200
    r = get(c, "/history/resampled", entity_id="sensor.gh_temp", step="24h", start=long_start, end=iso(T0), source="ha")
    assert r.status_code == 400 and "93 days" in r.json()["detail"]


def test_as_of_clamps_what_is_asked_of_vm(env):
    c, vm, _ = env
    r = c.get("/history/resampled", params={"entity_id": "sensor.gh_temp", "start": iso(T0), "end": iso(T0 + 10 * H)},
              headers={"X-As-Of": iso(T0 + 2 * H)})
    assert r.status_code == 200 and r.json()["end"] == T0 + 2 * H
    assert vm.requests[0]["end"] == T0 + 2 * H  # never asked for anything later
    assert [v for _, v in r.json()["series"][0]["points"]] == [60.0, 70.0]


def test_entity_not_in_vm_falls_back_to_home_assistant(env):
    c, _, ha = env
    r = get(c, "/history/resampled", entity_id="sensor.ha_only", step="1h", start=iso(T0), end=iso(T0 + 2 * H))
    s = r.json()["series"][0]
    assert r.status_code == 200 and s["source"] == "ha" and [v for _, v in s["points"]] == [5.0, 7.0]
    assert ha.calls and r.json()["warnings"] == []


def test_mixed_sources_in_one_request(env):
    c, _, _ = env
    r = get(c, "/history/resampled", entity_id="sensor.gh_temp,sensor.ha_only", step="1h", start=iso(T0),
            end=iso(T0 + 2 * H))
    assert [s["source"] for s in r.json()["series"]] == ["vm", "ha"]


def test_vm_down_falls_back_with_a_warning_and_source_vm_fails(env, monkeypatch):
    c, _, ha = env
    ha.rows["sensor.gh_temp"] = [(T0, "61"), (T0 + H, "62")]
    app.dependency_overrides[deps.get_vm] = FakeVM({}, status=401).client
    r = get(c, "/history/resampled", entity_id="sensor.gh_temp", step="1h", start=iso(T0), end=iso(T0 + 2 * H))
    assert r.status_code == 200 and r.json()["series"][0]["source"] == "ha"
    assert "not authorised" in r.json()["warnings"][0] and "sensor.gh_temp" in r.json()["warnings"][0]
    assert get(c, "/history/resampled", entity_id="sensor.gh_temp", source="vm").status_code == 502


def test_source_vm_errors_for_an_entity_vm_does_not_have(env):
    c, _, _ = env
    r = get(c, "/history/resampled", entity_id="sensor.ha_only", source="vm", start=iso(T0), end=iso(T0 + H))
    assert r.status_code == 404 and "sensor.ha_only" in r.json()["detail"]
    assert get(c, "/history/resampled", entity_id="sensor.gh_temp", source="nope").status_code == 400
    ha_only = get(c, "/history/resampled", entity_id="sensor.gh_temp", source="ha", start=iso(T0), end=iso(T0 + H))
    assert ha_only.json()["series"][0]["source"] == "ha"  # no HA history for it in this fake: empty, but asked of HA


def test_runtime_from_vm_on_off_values(env):
    c, vm, _ = env
    r = get(c, "/history/runtime", role="equipment_gaht_fan", days=1, end=iso(T0 + 12 * H))
    assert r.status_code == 200
    s = r.json()["series"][0]
    assert s["source"] == "vm" and s["days"][0]["hours_on"] == 3.0 and s["days"][0]["runs"] == 1
    assert s["first_sample"] == s["days"][0]["date"]
    assert vm.requests[0]["match"] == [FAN]


def test_runtime_reaches_beyond_93_days_on_vm(env):
    c, _, _ = env
    r = get(c, "/history/runtime", entity_id="switch.gaht", days=120, end=iso(T0 + 12 * H))
    assert r.status_code == 200 and len(r.json()["series"][0]["days"]) == 120
    assert get(c, "/history/runtime", entity_id="switch.gaht", days=401).status_code == 400
    assert get(c, "/history/runtime", entity_id="switch.gaht", days=94, source="ha").status_code == 400
