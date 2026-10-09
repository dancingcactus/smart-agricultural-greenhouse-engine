"""Translate Home Assistant add-on options (/data/options.json) into the env the app reads."""

from __future__ import annotations

import json
import os
from pathlib import Path


def apply_options(path: str | Path | None = None) -> bool:
    p = Path(path or os.environ.get("ADDON_OPTIONS", "/data/options.json"))
    if not p.exists():
        return False
    opts = json.loads(p.read_text())
    os.environ["VM_URL"] = opts["vm_url"]
    os.environ["VM_USERNAME"] = opts.get("vm_username") or ""
    os.environ["VM_PASSWORD"] = opts.get("vm_password") or ""
    os.environ["CATALOG_IGNORE"] = "\n".join(opts.get("catalog_ignore") or [])
    os.environ["HELPER_SNAPSHOT_ENABLED"] = "true" if opts.get("helper_snapshot_enabled") else ""
    os.environ["HELPER_SNAPSHOT_CRON"] = opts.get("helper_snapshot_cron") or "5 */6 * * *"
    os.environ["HELPER_SNAPSHOT_ENTITIES"] = "\n".join(opts.get("helper_snapshot_entities") or [])
    os.environ["WEATHER_ENTITIES"] = "\n".join(opts.get("weather_entities") or [])
    os.environ["WEATHER_CRON"] = opts.get("weather_cron") or "10 * * * *"
    os.environ["WEATHER_HOURLY_HORIZON_HOURS"] = str(opts.get("weather_hourly_horizon_hours") or 72)
    os.environ.setdefault("WEATHER_DB", "/data/weather_archive.sqlite3")
    os.environ["GATEWAY_API_KEY"] = opts.get("api_key") or ""
    os.environ["GATEWAY_AS_OF"] = opts.get("as_of_override") or ""
    os.environ["METRIC_CATALOG_CRON"] = opts["metric_catalog_cron"]
    os.environ["CATALOG_LOOKBACK_DAYS"] = str(opts["metric_catalog_lookback_days"])
    os.environ.setdefault("CATALOG_DB", "/data/metric_catalog.sqlite3")
    os.environ.setdefault("CATALOG_OUT_DIR", "/data/catalog")
    return True
