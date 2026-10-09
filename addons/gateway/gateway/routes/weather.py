"""Read-only access to the archived weather forecasts and observations."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from gateway import config
from gateway.asof import AsOfError, clip_end, parse_time
from gateway.deps import as_of
from gateway.weather import archive

router = APIRouter(prefix="/weather", tags=["weather archive"])


def _entity(entity_id: str | None) -> str:
    wanted = entity_id or (config.weather_entities() or [None])[0]
    if not wanted or not wanted.startswith("weather."):
        raise HTTPException(400, "give entity_id=weather.<name>, or configure weather_entities")
    return wanted


@router.get("/status")
def status():
    conn = archive.connect(config.weather_db())
    try:
        return {"configured": config.weather_entities(), **archive.status(conn)}
    finally:
        conn.close()


@router.get("/forecast")
def forecast(entity_id: str | None = None, type: str = "hourly", horizon_hours: int | None = None,
             as_of_ts: float | None = Depends(as_of)):
    """The forecast as it was known at the as-of time (default: now): never a later one."""
    if type not in ("hourly", "daily", "twice_daily"):
        raise HTTPException(400, "type must be hourly, daily or twice_daily")
    at = as_of_ts if as_of_ts is not None else time.time()
    conn = archive.connect(config.weather_db())
    try:
        found = archive.forecast_as_of(conn, _entity(entity_id), type, at, horizon_hours)
    finally:
        conn.close()
    if found is None:
        raise HTTPException(404, "no forecast had been archived by that time")
    return found


@router.get("/observations")
def observations(entity_id: str | None = None, start: str | None = None, end: str | None = None,
                 limit: int = 1000, as_of_ts: float | None = Depends(as_of)):
    try:
        end_ts = clip_end(parse_time(end) if end else None, as_of_ts, time.time())
        start_ts = parse_time(start) if start else end_ts - 86400
    except AsOfError as exc:
        raise HTTPException(400, str(exc)) from exc
    conn = archive.connect(config.weather_db())
    try:
        return archive.observations(conn, _entity(entity_id), start_ts, end_ts, max(1, min(limit, 5000)))
    finally:
        conn.close()
