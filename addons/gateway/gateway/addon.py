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
    os.environ["METRIC_CATALOG_CRON"] = opts["metric_catalog_cron"]
    os.environ["CATALOG_LOOKBACK_DAYS"] = str(opts["metric_catalog_lookback_days"])
    os.environ.setdefault("CATALOG_DB", "/data/metric_catalog.sqlite3")
    os.environ.setdefault("CATALOG_OUT_DIR", "/data/catalog")
    return True
