from pathlib import Path

import httpx
import pytest
import respx
import yaml
from fastapi.testclient import TestClient

from gateway.main import app
from gateway.metric_catalog import store
from gateway.metric_catalog.collect import collect_entities
from gateway.metric_catalog.mirror import mirror_yaml, select_entities
from gateway.metric_catalog.models import EntityRecord
from gateway.metric_catalog.runner import config_from_env
from gateway.metric_catalog.vm_match import VMClient, match_series

NOW = 1_800_000_000.0


class HA:
    """Two helpers: one with its own history, one only visible through its snapshot sensor."""

    def ws_list(self, cmd):
        return {
            "config/label_registry/list": [],
            "config/area_registry/list": [],
            "config/device_registry/list": [],
            "config/entity_registry/list": [
                {"entity_id": "input_number.hot_alarm", "name": "Hot Alarm"},
                {"entity_id": "input_boolean.heater", "name": "Heater"},
                {"entity_id": "sensor.small_screen_uptime", "name": "Uptime"},
            ],
        }[cmd]

    def states(self):
        return [
            {"entity_id": "input_number.hot_alarm", "attributes": {"friendly_name": "Hot Alarm", "unit_of_measurement": "°F"}},
            {"entity_id": "input_boolean.heater", "attributes": {"friendly_name": "Heater"}},
            {"entity_id": "sensor.hot_alarm_snapshot",
             "attributes": {"friendly_name": "Hot Alarm Snapshot", "source_entity": "input_number.hot_alarm"}},
            {"entity_id": "sensor.small_screen_uptime", "attributes": {"friendly_name": "Uptime"}},
        ]


def _match(ignore=None):
    ents = collect_entities(HA())
    series = [{"__name__": "°F_value", "entity_id": "hot_alarm_snapshot", "domain": "sensor"},
              {"__name__": "°F_value", "entity_id": "gone_sensor", "domain": "sensor"}]
    with respx.mock(base_url="http://vm", assert_all_called=False) as router:
        router.get("/api/v1/labels").respond(json={"status": "success", "data": ["entity_id"]})
        router.get("/api/v1/series").respond(json={"status": "success", "data": series})
        router.get("/api/v1/query").mock(side_effect=lambda req: httpx.Response(200, json={
            "status": "success", "data": {"result": []}}))
        _, found = match_series(VMClient("http://vm"), ents, now=NOW, ignore=ignore)
    return ents, found


def test_snapshot_links_to_its_helper_and_covers_it():
    ents, _ = _match()
    assert ents["sensor.hot_alarm_snapshot"].mirror_of == "input_number.hot_alarm"
    assert ents["input_number.hot_alarm"].mirrored_by == "sensor.hot_alarm_snapshot"
    assert ents["input_number.hot_alarm"].vm_status == "mirrored"  # no data of its own, but covered
    assert ents["input_boolean.heater"].vm_status == "missing"  # nothing mirrors it


def test_ignore_patterns_beat_other_statuses():
    ents, _ = _match(ignore=["sensor.small_screen_*", "sensor.gone_*", "  "])
    assert ents["sensor.small_screen_uptime"].vm_status == "ignored"
    assert ents["sensor.gone_sensor"].vm_status == "ignored"  # an orphan can be ignored too
    assert ents["input_boolean.heater"].vm_status == "missing"
    assert ents["sensor.hot_alarm_snapshot"].vm_status == "ok"


def test_ignored_entities_leave_the_missing_counts(tmp_path):
    ents, series = _match(ignore=["sensor.small_screen_*"])
    conn = store.connect(tmp_path / "c.sqlite3")
    store.save_run(conn, "influxdb", ents, series)
    s = store.summary(conn)
    assert s["by_status"]["ignored"] == 1 and s["by_status"]["mirrored"] == 1
    assert "sensor" not in s["by_domain"] or s["by_domain"]["sensor"].get("missing", 0) == 0
    assert "sensor.small_screen_uptime" in {e["entity_id"] for e in store.search(conn, "uptime")}  # still findable


def _rows(**over):
    base = {"entity_id": "input_number.hot_alarm", "friendly_name": "Hot Alarm", "name": "Hot Alarm", "unit": "°F",
            "vm_status": "missing", "has_state": 1, "disabled": 0, "mirrored_by": "", "last_value_seen": None}
    return [base | over]


def test_mirror_yaml_is_valid_and_complete():
    rows = _rows() + _rows(entity_id="input_number.leaf_offset", unit="", friendly_name="Leaf Offset") \
        + _rows(entity_id="input_boolean.heater", unit="", friendly_name="Heater")
    doc = yaml.safe_load(mirror_yaml(rows, hours=6))
    block = doc["template"][0]
    assert block["triggers"][0] == {"trigger": "time_pattern", "hours": "/6", "minutes": "0"}
    assert block["triggers"][1] == {"trigger": "homeassistant", "event": "start"}
    a, b, c = block["sensor"]
    assert a["name"] == "Hot Alarm Snapshot" and a["unique_id"] == "hot_alarm_snapshot"
    assert a["unit_of_measurement"] == "°F" and a["state_class"] == "measurement"
    assert a["state"] == "{{ states('input_number.hot_alarm') }}"
    assert a["attributes"]["source_entity"] == "input_number.hot_alarm"
    assert "recorded_at" in a["attributes"]
    assert "unit_of_measurement" not in b  # helpers without a unit get none
    assert "state_class" not in c and "is_state('input_boolean.heater', 'on')" in c["state"]
    assert "not in ['unknown', 'unavailable']" in a["availability"]


def test_mirror_yaml_hours_and_empty():
    assert yaml.safe_load(mirror_yaml(_rows(), hours=24))["template"][0]["triggers"][0]["hours"] == "3"
    with pytest.raises(ValueError, match="hours"):
        mirror_yaml(_rows(), hours=5)  # would not divide the day evenly
    assert yaml.safe_load(mirror_yaml([]))["template"][0]["sensor"] == []


def test_select_entities_rules():
    old, recent = NOW - 90 * 86400, NOW - 3600
    rows = (
        _rows(entity_id="input_number.never")                                             # missing -> yes
        + _rows(entity_id="input_number.stale", vm_status="ok", last_value_seen=old)     # old data -> yes
        + _rows(entity_id="input_number.fresh", vm_status="ok", last_value_seen=recent)  # fresh -> only if "all"
        + _rows(entity_id="input_number.done", mirrored_by="sensor.done_snapshot")       # already mirrored
        + _rows(entity_id="input_number.off", disabled=1)
        + _rows(entity_id="input_number.nostate", has_state=0)
        + _rows(entity_id="input_number.skipped", vm_status="ignored")
        + _rows(entity_id="sensor.not_a_helper")
    )
    stale = {r["entity_id"] for r in select_entities(rows, "stale", 30, now=NOW)}
    assert stale == {"input_number.never", "input_number.stale"}
    everything = {r["entity_id"] for r in select_entities(rows, "all", 30, now=NOW)}
    assert everything == stale | {"input_number.fresh"}


def test_mirror_route_end_to_end(tmp_path, monkeypatch):
    db = tmp_path / "c.sqlite3"
    monkeypatch.setenv("CATALOG_DB", str(db))
    ents, series = _match()
    conn = store.connect(db)
    store.save_run(conn, "influxdb", ents, series)
    client = TestClient(app)
    r = client.get("/catalog/mirror.yaml", params={"only": "all"})
    assert r.status_code == 200 and "text/yaml" in r.headers["content-type"]
    names = [s["name"] for s in yaml.safe_load(r.text)["template"][0]["sensor"]]
    assert names == ["Heater Snapshot"]  # hot_alarm is already mirrored
    assert client.get("/catalog/mirror.yaml", params={"only": "nope"}).status_code == 400
    assert client.get("/catalog/mirror.yaml", params={"hours": 5}).status_code == 400


def test_defaults_and_ignore_env(monkeypatch):
    for k in ("CATALOG_LOOKBACK_DAYS", "CATALOG_IGNORE"):
        monkeypatch.delenv(k, raising=False)
    assert config_from_env()["lookback_days"] == 365
    monkeypatch.setenv("CATALOG_IGNORE", "sensor.a_*\n\nsensor.b_*")
    assert config_from_env()["ignore"] == ["sensor.a_*", "sensor.b_*"]
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    assert cfg["options"]["metric_catalog_lookback_days"] == 365 and cfg["options"]["catalog_ignore"] == []


def test_unmirrored_record_defaults():
    assert EntityRecord("input_number.x").mirrored_by == ""


def test_addon_config_is_valid_and_options_match_schema():
    cfg = yaml.safe_load(Path("config.yaml").read_text())  # a broken file would break the install
    assert set(cfg["options"]) <= set(cfg["schema"])
    assert cfg["schema"]["catalog_ignore"] == ["str"]
    assert cfg["init"] is False and cfg["homeassistant_api"] is True
