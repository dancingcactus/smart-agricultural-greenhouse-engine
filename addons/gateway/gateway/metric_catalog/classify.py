"""Classify InfluxDB-style series fields and explain why an entity has no numeric data."""

from __future__ import annotations

from .models import EntityRecord

NON_NUMERIC_DOMAINS = frozenset({
    "automation", "script", "scene", "button", "update", "person", "device_tracker", "text",
    "select", "event", "notify", "tts", "stt", "conversation", "todo", "calendar", "image",
    "camera", "weather", "sun", "zone", "input_text", "input_select", "input_button",
    "input_datetime", "remote", "media_player", "ai_task", "assist_satellite", "wake_word",
})
NON_NUMERIC_CLASSES = frozenset({"timestamp", "date", "enum"})


def field_and_kind(metric: str, entity_id: str, unit: str, ingest_mode: str) -> tuple[str, str]:
    """Split an InfluxDB metric name into (field, kind).

    HA names the measurement after the unit when there is one ("W_value", "°F_value") and after
    the entity ID otherwise ("automation.x_current"). String fields carry a "_str" suffix.
    """
    if ingest_mode == "prometheus":
        return "", "value"
    field = ""
    for prefix in (f"{unit}_" if unit else None, f"{entity_id}_"):
        if prefix and metric.startswith(prefix):
            field = metric[len(prefix):]
            break
    else:
        field = metric.split("_", 1)[-1]
    if field == "value":
        return field, "value"
    if field == "state":
        return field, "state"
    if field.endswith("_str"):
        return field, "attribute_str"
    return field, "attribute_num"


def missing_reason(ent: EntityRecord) -> str:
    if ent.disabled:
        return "disabled"
    if not ent.has_state:
        return "no_state"
    domain = ent.entity_id.split(".", 1)[0]
    if domain in NON_NUMERIC_DOMAINS or ent.device_class in NON_NUMERIC_CLASSES:
        return "non_numeric"
    return "not_exported"
