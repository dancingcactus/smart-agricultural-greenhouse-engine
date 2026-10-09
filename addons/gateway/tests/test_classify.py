import sqlite3

import pytest

from gateway.metric_catalog import store
from gateway.metric_catalog.classify import field_and_kind, missing_reason
from gateway.metric_catalog.models import EntityRecord, SeriesRecord


@pytest.mark.parametrize(
    ("metric", "entity_id", "unit", "expected"),
    [
        ("W_value", "sensor.plug_a_power", "W", ("value", "value")),
        ("°F_value", "sensor.zone_a_temperature", "°F", ("value", "value")),
        ("W_device_class_str", "sensor.plug_a_power", "W", ("device_class_str", "attribute_str")),
        ("°F_friendly_name_str", "sensor.zone_a_temperature", "°F", ("friendly_name_str", "attribute_str")),
        ("automation.example_reminder_current", "automation.example_reminder", "", ("current", "attribute_num")),
        ("automation.example_reminder_last_triggered_str", "automation.example_reminder", "",
         ("last_triggered_str", "attribute_str")),
        ("sensor.example_status_state", "sensor.example_status", "", ("state", "state")),
    ],
)
def test_field_and_kind_on_real_names(metric, entity_id, unit, expected):
    assert field_and_kind(metric, entity_id, unit, "influxdb") == expected


def test_prometheus_series_are_values():
    assert field_and_kind("homeassistant_sensor_temperature_celsius", "sensor.x", "°C", "prometheus") == ("", "value")


def test_missing_reasons():
    assert missing_reason(EntityRecord("sensor.a", disabled=True)) == "disabled"
    assert missing_reason(EntityRecord("sensor.example_addon_cpu", in_registry=True)) == "no_state"
    assert missing_reason(EntityRecord("sensor.example_next_run", has_state=True, device_class="timestamp")) == "non_numeric"
    assert missing_reason(EntityRecord("button.restart", has_state=True)) == "non_numeric"
    assert missing_reason(EntityRecord("sensor.temp", has_state=True, device_class="temperature")) == "not_exported"


def test_summary_counts_and_orphans(tmp_path):
    conn = store.connect(tmp_path / "c.sqlite3")
    ents = {
        "sensor.a": EntityRecord("sensor.a", vm_status="ok"),
        "sensor.b": EntityRecord("sensor.b", vm_status="missing", vm_reason="no_state"),
        "button.c": EntityRecord("button.c", vm_status="missing", vm_reason="non_numeric"),
        "sensor.old": EntityRecord("sensor.old", vm_status="orphan"),
    }
    store.save_run(conn, "influxdb", ents, [SeriesRecord("sensor.a", "W_value", {}, "{}", field="value", kind="value")])
    s = store.summary(conn)
    assert s["by_status"] == {"ok": 1, "missing": 2, "orphan": 1}
    assert s["missing_by_reason"] == {"no_state": 1, "non_numeric": 1}
    assert s["by_domain"]["sensor"] == {"ok": 1, "missing": 1, "orphan": 1}
    assert s["orphans"] == ["sensor.old"]
    assert store.get_entity(conn, "sensor.a")["series"][0]["kind"] == "value"


def test_old_database_is_migrated(tmp_path):
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript(
        "CREATE TABLE entities(run_id INTEGER, entity_id TEXT);"
        "CREATE TABLE series(run_id INTEGER, entity_id TEXT, metric TEXT);"
    )
    old.close()
    conn = store.connect(path)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(series)")}
    assert {"field", "kind", "history_first_seen"} <= cols
    assert "vm_reason" in {r["name"] for r in conn.execute("PRAGMA table_info(entities)")}
