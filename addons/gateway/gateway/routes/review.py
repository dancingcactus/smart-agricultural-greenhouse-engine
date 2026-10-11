"""The daily review's plain-code summary, and which loops the current mode allows. Read-only; no model is involved."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from gateway import config, review, watch
from gateway.deps import as_of, get_ha
from gateway.glossary import store
from gateway.ha_client import HAClient
from gateway.routes.ha import _live_only, _upstream
from gateway.routes.watch import _history
from gateway.weather import archive

router = APIRouter(prefix="/review", tags=["review"])


def _roles_and_siblings():
    conn = store.connect(config.glossary_db())
    try:
        return store.roles(conn), {e["entity_id"]: e["same_device"] for e in store.list_entries(conn) if e["same_device"]}
    finally:
        conn.close()


@router.get("/cadence")
def cadence(ha: HAClient = Depends(get_ha), as_of_ts: float | None = Depends(as_of)):
    """The current mode and which loops (watch, daily, evening, weekly) it allows. Each run calls this first."""
    _live_only(as_of_ts)
    roles, _ = _roles_and_siblings()
    mode_id = watch._first(roles, "mode_switch")
    if not mode_id:
        return {**review.loops_due(None), "note": "no approved mode_switch role, so the growing cadence is assumed"}
    state = next((s for s in _upstream(ha.states) if s["entity_id"] == mode_id), None)
    return review.loops_due((state or {}).get("state"))


@router.get("/daily")
def daily(hours: float = 24, as_of_ts: float | None = Depends(as_of), ha: HAClient = Depends(get_ha)):
    """The last `hours` of the greenhouse in numbers, the watch's findings, and the 72-hour forecast risks."""
    _live_only(as_of_ts)
    if not 1 <= hours <= 72:
        raise HTTPException(400, "hours must be between 1 and 72")
    roles, siblings = _roles_and_siblings()
    now = time.time()
    states = {s["entity_id"]: s for s in _upstream(ha.states)}
    ids = sorted({e["entity_id"] for r, es in roles.items()
                  if watch.is_measurement(r) or r == "greenhouse_temperature" or r.startswith("equipment_") for e in es})
    history = _history(ha, ids, now - max(hours, 4) * 3600 - 3600, now)
    watch_result = watch.run(now, roles, states, history, siblings)
    forecast = None
    if config.weather_entities():
        conn = archive.connect(config.weather_db())
        try:
            forecast = archive.forecast_as_of(conn, config.weather_entities()[0], "hourly", now, review.FORECAST_HOURS)
        finally:
            conn.close()
    try:
        tz = _upstream(lambda: ha.get("/config")).get("time_zone")
    except HTTPException:
        tz = None
    return review.daily(now, roles, states, history, watch_result, forecast, tz, hours)
