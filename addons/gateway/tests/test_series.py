from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from gateway import config, deps, series
from gateway.asof import iso
from gateway.glossary import store
from gateway.main import app
from gateway.metric_catalog import store as catalog_store
from gateway.metric_catalog.models import EntityRecord

T0 = 1_800_000_000.0 - 1_800_000_000.0 % 86400  # a UTC midnight
H = 3600
UTC = ZoneInfo("UTC")


def test_step_seconds():
    assert series.step_seconds("15m") == 900 and series.step_seconds("1h") == 3600
    for bad in ("1s", "90", "2d", "0h", "25h", "abc"):
        with pytest.raises(series.SeriesError):
            series.step_seconds(bad)


def test_resample_is_a_time_weighted_mean_on_whole_buckets():
    pts = [(T0, "10"), (T0 + 1800, "20"), (T0 + 2 * H, "30")]
    out = series.resample(pts, T0, T0 + 3 * H + 600, 3600)  # the partial 4th hour is not returned
    assert out == [[T0, 15.0], [T0 + H, 20.0], [T0 + 2 * H, 30.0]]


def test_resample_leaves_gaps_as_gaps_and_ignores_non_numbers():
    pts = [(T0, "10"), (T0 + H, "unavailable"), (T0 + 2 * H, "12"), (T0 + 3 * H, "nan")]
    out = series.resample(pts, T0, T0 + 4 * H, 3600)
    assert [v for _, v in out] == [10.0, None, 12.0, None]


def test_resample_starts_on_the_next_boundary_and_caps_size():
    assert series.resample([(T0 - H, "5")], T0 + 100, T0 + 2 * H + 100, 3600)[0][0] == T0 + H
    assert series.resample([], T0, T0, 3600) == []
    with pytest.raises(series.SeriesError):
        series.resample([], T0, T0 + 400 * 86400, 300)


def test_daily_runtime_counts_hours_runs_and_completeness():
    pts = [(T0, "off"), (T0 + 2 * H, "on"), (T0 + 5 * H, "off"), (T0 + 30 * H, "on")]
    days = series.daily_runtime(pts, T0, T0 + 36 * H, UTC)
    assert len(days) == 2 and days[0]["date"] < days[1]["date"]
    assert days[0] == {"date": days[0]["date"], "hours_on": 3.0, "runs": 1, "known_hours": 24.0, "complete": True}
    assert days[1]["hours_on"] == 6.0 and days[1]["complete"] is False  # window ends at noon


def test_daily_runtime_marks_days_with_unknown_state_incomplete():
    pts = [(T0, "unavailable"), (T0 + 12 * H, "off")]
    day = series.daily_runtime(pts, T0, T0 + 24 * H, UTC)[0]
    assert day["known_hours"] == 12.0 and day["complete"] is False and day["hours_on"] == 0


class FakeHA:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def get(self, path, **params):
        self.calls.append((path, params))
        if path == "/config":
            return {"time_zone": "UTC"}
        ids = params["filter_entity_id"].split(",")
        return [[{"entity_id": eid, "state": s, "last_changed": iso(t)} for t, s in self.rows[eid]]
                for eid in ids if eid in self.rows]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOSSARY_DB", str(tmp_path / "g.sqlite3"))
    cat = catalog_store.connect(str(tmp_path / "c.sqlite3"))
    catalog_store.save_run(cat, "influxdb", {
        "sensor.gh_temp": EntityRecord("sensor.gh_temp", friendly_name="GH Temp", unit="°F", vm_status="ok"),
        "switch.gaht": EntityRecord("switch.gaht", friendly_name="GAHT fan", vm_status="ok")}, [])
    conn = store.connect(config.glossary_db())
    store.sync(cat, conn, {}, now=T0)
    for eid, role in (("sensor.gh_temp", "greenhouse_temperature"), ("switch.gaht", "equipment_gaht_fan")):
        store.owner_update(conn, eid, "Sam", meaning=f"the {role}", aliases=[f"role:{role}"], status="approved")
    conn.close()
    cat.close()
    ha = FakeHA({"sensor.gh_temp": [(T0, "60"), (T0 + H, "70")],
                 "switch.gaht": [(T0, "off"), (T0 + 2 * H, "on"), (T0 + 5 * H, "off")]})
    app.dependency_overrides[deps.get_ha] = lambda: ha
    yield TestClient(app), ha
    app.dependency_overrides.clear()


def test_resampled_route_by_entity(client):
    c, _ = client
    r = c.get("/history/resampled", params={"entity_id": "sensor.gh_temp", "step": "1h",
                                            "start": iso(T0), "end": iso(T0 + 3 * H)})
    assert r.status_code == 200
    body = r.json()
    assert body["step_seconds"] == 3600 and body["series"][0]["points"] == [[T0, 60.0], [T0 + H, 70.0], [T0 + 2 * H, 70.0]]


def test_resampled_route_clips_to_as_of_and_never_asks_for_later_data(client):
    c, ha = client
    r = c.get("/history/resampled", params={"entity_id": "sensor.gh_temp", "start": iso(T0), "end": iso(T0 + 10 * H)},
              headers={"X-As-Of": iso(T0 + 2 * H)})
    assert r.status_code == 200 and r.json()["end"] == T0 + 2 * H and len(r.json()["series"][0]["points"]) == 2
    assert ha.calls[-1][1]["end_time"] == iso(T0 + 2 * H)


def test_resampled_route_rejects_bad_requests(client):
    c, _ = client
    assert c.get("/history/resampled").status_code == 400  # neither entity nor role
    assert c.get("/history/resampled", params={"entity_id": "sensor.a", "role": "x"}).status_code == 400
    assert c.get("/history/resampled", params={"entity_id": "sensor.a", "step": "3d"}).status_code == 400
    assert c.get("/history/resampled", params={"role": "nonexistent_role"}).status_code == 404


def test_runtime_route_by_entity_and_clipping(client):
    c, _ = client
    r = c.get("/history/runtime", params={"entity_id": "switch.gaht", "days": 1, "end": iso(T0 + 12 * H)})
    assert r.status_code == 200
    day = r.json()["series"][0]["days"][0]
    assert day["hours_on"] == 3.0 and day["runs"] == 1 and day["complete"] is False
    assert c.get("/history/runtime", params={"entity_id": "switch.gaht", "days": 0}).status_code == 400
    clipped = c.get("/history/runtime", params={"entity_id": "switch.gaht", "days": 1},
                    headers={"X-As-Of": iso(T0 + 4 * H)}).json()
    assert clipped["end"] == T0 + 4 * H and clipped["series"][0]["days"][0]["hours_on"] == 2.0


def test_routes_resolve_roles(client):
    c, _ = client
    r = c.get("/history/resampled", params={"role": "greenhouse_temperature", "start": iso(T0), "end": iso(T0 + 2 * H)})
    assert r.status_code == 200 and r.json()["role"] == "greenhouse_temperature"
    assert r.json()["series"][0]["entity_id"] == "sensor.gh_temp"
    r = c.get("/history/runtime", params={"role": "equipment_gaht_fan", "days": 1, "end": iso(T0 + 12 * H)})
    assert r.status_code == 200 and r.json()["series"][0]["days"][0]["hours_on"] == 3.0
