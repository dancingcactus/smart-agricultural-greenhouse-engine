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


def call_log_path() -> Path | None:
    raw = os.environ.get("GATEWAY_CALL_LOG")
    if raw:
        return Path(raw)
    return Path("/data/calls.jsonl") if Path("/data").is_dir() else None
