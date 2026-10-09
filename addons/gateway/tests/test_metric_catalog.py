
import httpx
import pytest
import respx

from gateway.ha_client import HAClient
from gateway.metric_catalog import store
from gateway.metric_catalog.collect import collect_entities
from gateway.metric_catalog.models import EntityRecord
from gateway.metric_catalog.runner import run_once
from gateway.metric_catalog.vm_match import (
    VMClient,
    detect_ingest_mode,
    entity_id_from_labels,
    match_series,
)

NOW = 1_800_000_000.0


class FakeHA:
    def ws_list(self, cmd):
        return {
            "config/label_registry/list": [{"label_id": "agent_managed", "name": "agent-managed"}],
            "config/area_registry/list": [{"area_id": "zone1", "name": "Zone 1"}],
            "config/device_registry/list": [{"id": "dev1", "area_id": "zone1"}],
            "config/entity_registry/list": [
                {"entity_id": "sensor.zone1_temp", "original_name": "Zone 1 Temperature",
                 "labels": ["agent_managed"], "original_icon": "mdi:thermometer", "platform": "mqtt",
                 "device_id": "dev1", "device_class": "temperature"},
                {"entity_id": "switch.vent_fan", "name": "Vent fan", "icon": "mdi:fan", "platform": "mqtt"},
            ],
        }[cmd]

    def states(self):
        return [
            {"entity_id": "sensor.zone1_temp", "attributes": {
                "friendly_name": "Zone 1 Temperature", "unit_of_measurement": "°C", "state_class": "measurement"}},
            {"entity_id": "sensor.yaml_only", "attributes": {"friendly_name": "YAML only"}},
        ]


def _series(mode):
    if mode == "prometheus":
        return [
            {"__name__": "homeassistant_sensor_temperature_celsius", "entity": "sensor.zone1_temp"},
            {"__name__": "homeassistant_sensor_temperature_celsius", "entity": "sensor.old_renamed"},
        ]
    return [
        {"__name__": "°C_value", "entity_id": "zone1_temp", "domain": "sensor"},
        {"__name__": "°C_value", "entity_id": "old_renamed", "domain": "sensor"},
    ]


def _mock_vm(router, mode):
    router.get("/api/v1/labels").respond(json={"status": "success", "data": ["__name__", "entity" if mode == "prometheus" else "entity_id"]})
    router.get("/api/v1/series").respond(json={"status": "success", "data": _series(mode)})

    def instant(request):
        q = request.url.params["query"]
        fn = q.split("(")[0]
        value = {"count_over_time": "101", "tfirst_over_time": str(NOW - 1000), "tlast_over_time": str(NOW)}[fn]
        return httpx.Response(200, json={"status": "success", "data": {"result": [
            {"metric": {k: v for k, v in s.items() if k != "__name__"}, "value": [NOW, value]}
            for s in _series(mode) if s["entity" if mode == "prometheus" else "entity_id"].endswith("zone1_temp")
        ]}})
    router.get("/api/v1/query").mock(side_effect=instant)


@pytest.fixture(params=["prometheus", "influxdb"])
def matched(request):
    ents = collect_entities(FakeHA())
    with respx.mock(base_url="http://vm") as router:
        _mock_vm(router, request.param)
        mode, series = match_series(VMClient("http://vm"), ents, now=NOW)
    return request.param, mode, series, ents


def test_collect_merges_registry_and_states():
    ents = collect_entities(FakeHA())
    t = ents["sensor.zone1_temp"]
    assert (t.labels, t.icon, t.area, t.unit) == (["agent-managed"], "mdi:thermometer", "Zone 1", "°C")
    assert ents["sensor.yaml_only"].in_registry is False
    assert ents["switch.vent_fan"].icon == "mdi:fan"


def test_ingest_mode_and_entity_id_derivation():
    assert detect_ingest_mode({"entity"}, ["homeassistant_sensor_x"]) == "prometheus"
    assert detect_ingest_mode({"entity_id"}, ["°C_value"]) == "influxdb"
    assert detect_ingest_mode({"foo"}, []) == "unknown"
    assert entity_id_from_labels({"entity_id": "x", "domain": "sensor"}) == "sensor.x"
    assert entity_id_from_labels({"job": "x"}) is None


def test_match_status_and_stats(matched):
    param, mode, series, ents = matched
    assert mode == param
    assert ents["sensor.zone1_temp"].vm_status == "ok"
    assert ents["switch.vent_fan"].vm_status == "missing"
    assert ents["sensor.old_renamed"].vm_status == "orphan"
    s = next(x for x in series if x.entity_id == "sensor.zone1_temp")
    assert s.samples == 101 and s.avg_interval_s == pytest.approx(10.0)
    assert "sensor.zone1_temp" in s.selector or 'entity_id="zone1_temp"' in s.selector


def test_search_by_name_label_icon_metric(matched, tmp_path):
    _, mode, series, ents = matched
    conn = store.connect(tmp_path / "c.sqlite3")
    store.save_run(conn, mode, ents, series)
    assert store.search(conn, "temperature")[0]["entity_id"] == "sensor.zone1_temp"
    assert store.search(conn, "agent-managed")[0]["entity_id"] == "sensor.zone1_temp"
    assert store.search(conn, "mdi:fan")[0]["entity_id"] == "switch.vent_fan"
    assert store.search(conn, "vent fan")[0]["entity_id"] == "switch.vent_fan"
    assert store.search(conn, 'vent" OR (') == []  # FTS syntax is quoted, not interpreted
    assert store.get_entity(conn, "nope") is None
    assert store.get_entity(conn, "sensor.zone1_temp")["series"][0]["samples"] == 101


def test_diff_between_runs(tmp_path):
    conn = store.connect(tmp_path / "c.sqlite3")
    a = {"sensor.a": EntityRecord("sensor.a", name="A", vm_status="ok"),
         "sensor.gone": EntityRecord("sensor.gone")}
    store.save_run(conn, "prometheus", a, [])
    b = {"sensor.a": EntityRecord("sensor.a", name="A renamed", vm_status="ok"),
         "sensor.new": EntityRecord("sensor.new")}
    store.save_run(conn, "prometheus", b, [])
    d = store.diff_runs(conn)
    assert d["new"] == ["sensor.new"] and d["removed"] == ["sensor.gone"]
    assert d["changed"][0]["changes"]["name"] == ["A", "A renamed"]


def test_run_once_exports_files(tmp_path, monkeypatch):
    with respx.mock(base_url="http://vm") as router:
        _mock_vm(router, "prometheus")
        monkeypatch.setattr("gateway.metric_catalog.runner.collect_entities", lambda ha: collect_entities(FakeHA()))
        monkeypatch.setattr("gateway.metric_catalog.vm_match.time.time", lambda: NOW)
        result = run_once(None, VMClient("http://vm"), tmp_path / "c.sqlite3", tmp_path / "out")
    assert result["ingest_mode"] == "prometheus" and result["series"] == 2
    assert "sensor.zone1_temp" in (tmp_path / "out/entity_metrics.jsonl").read_text()
    assert (tmp_path / "out/entity_metrics.csv").read_text().startswith("entity_id,")


def test_ha_client_has_no_write_or_unlisted_ws():
    ha = HAClient("http://ha", "t")
    assert not any(hasattr(ha, n) for n in ("post", "call_service", "put", "delete"))
    with pytest.raises(PermissionError):
        ha.ws_list("call_service")


def test_unreachable_vm_error_names_the_url():
    with respx.mock(base_url="http://vm") as router:
        router.get("/api/v1/series").mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(RuntimeError, match="http://vm"):
            VMClient("http://vm").series(0)
