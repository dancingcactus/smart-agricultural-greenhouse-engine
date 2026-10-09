"""Runtime settings, read from the environment on each call so tests and options can change them."""

from __future__ import annotations

import os
from pathlib import Path

from gateway.asof import parse_time


def api_key() -> str:
    return os.environ.get("GATEWAY_API_KEY", "")


def forced_as_of() -> float | None:
    raw = os.environ.get("GATEWAY_AS_OF", "")
    return parse_time(raw) if raw else None


def catalog_db() -> str:
    return os.environ.get("CATALOG_DB", "metric_catalog.sqlite3")


def call_log_path() -> Path | None:
    raw = os.environ.get("GATEWAY_CALL_LOG")
    if raw:
        return Path(raw)
    return Path("/data/calls.jsonl") if Path("/data").is_dir() else None


def snapshot_enabled() -> bool:
    return os.environ.get("HELPER_SNAPSHOT_ENABLED", "").lower() in ("1", "true", "yes")


def snapshot_patterns() -> list[str]:
    return [p.strip() for p in os.environ.get("HELPER_SNAPSHOT_ENTITIES", "").splitlines() if p.strip()]


def snapshot_cron() -> str:
    return os.environ.get("HELPER_SNAPSHOT_CRON", "5 */6 * * *")


def weather_entities() -> list[str]:
    return [p.strip() for p in os.environ.get("WEATHER_ENTITIES", "").splitlines() if p.strip()]


def weather_cron() -> str:
    return os.environ.get("WEATHER_CRON", "10 * * * *")


def weather_horizon_hours() -> int:
    return int(os.environ.get("WEATHER_HOURLY_HORIZON_HOURS", "72"))


def weather_db() -> str:
    return os.environ.get("WEATHER_DB", "weather_archive.sqlite3")
