"""Evenly spaced history for forecasting. Read-only; the forecasts themselves are made on the agent side."""

from __future__ import annotations

import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException

from gateway import config, series
from gateway.asof import clip_end
from gateway.deps import as_of, entity_ids, get_ha
from gateway.glossary import store
from gateway.ha_client import HAClient
from gateway.routes.ha import MAX_SPAN_S, _upstream, _window
from gateway.routes.watch import _history

router = APIRouter(prefix="/history", tags=["history for forecasting"])

MAX_ENTITIES = 5


def _targets(entity_id: str | None, role: str | None, as_of_ts: float | None) -> tuple[list[str], str | None]:
    if bool(entity_id) == bool(role):
        raise HTTPException(400, "give exactly one of entity_id or role")
    if entity_id:
        return entity_ids(entity_id, limit=MAX_ENTITIES), None
    conn = store.connect(config.glossary_db())
    try:
        found = store.roles(conn, as_of_ts).get(role, [])
    finally:
        conn.close()
    if not found:
        raise HTTPException(404, f"no approved entity has the role {role!r}")
    return [e["entity_id"] for e in found][:MAX_ENTITIES], role


@router.get("/resampled")
def resampled(entity_id: str | None = None, role: str | None = None, step: str = "1h",
              start: str | None = None, end: str | None = None,
              as_of_ts: float | None = Depends(as_of), ha: HAClient = Depends(get_ha)):
    """Numeric history as time-weighted means on a fixed grid, [bucket_start_epoch, value] with null for gaps."""
    try:
        seconds = series.step_seconds(step)
    except series.SeriesError as exc:
        raise HTTPException(400, str(exc)) from exc
    ids, role_name = _targets(entity_id, role, as_of_ts)
    start_ts, end_ts = _window(start, end, as_of_ts)
    history = _history(ha, ids, start_ts, end_ts)
    try:
        out = [{"entity_id": eid, "points": series.resample(history.get(eid, []), start_ts, end_ts, seconds)}
               for eid in ids]
    except series.SeriesError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"role": role_name, "step_seconds": seconds, "start": start_ts, "end": end_ts, "as_of": as_of_ts,
            "series": out}


def _tz(ha: HAClient, name: str | None):
    if not name:
        try:
            name = _upstream(lambda: ha.get("/config")).get("time_zone")
        except HTTPException:
            name = None
    try:
        return ZoneInfo(name) if name else ZoneInfo("UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


@router.get("/runtime")
def runtime(entity_id: str | None = None, role: str | None = None, days: int = 14, end: str | None = None,
            timezone: str | None = None, as_of_ts: float | None = Depends(as_of), ha: HAClient = Depends(get_ha)):
    """Hours switched on per local day (for example the GAHT fan), with run counts. The last day may be partial."""
    if not 1 <= days <= MAX_SPAN_S // 86400:
        raise HTTPException(400, f"days must be between 1 and {MAX_SPAN_S // 86400}")
    ids, role_name = _targets(entity_id, role, as_of_ts)
    tz = _tz(ha, timezone)
    end_ts = clip_end(_window(None, end, as_of_ts)[1] if end else None, as_of_ts, time.time())
    midnight = _midnight(end_ts, tz)
    start_ts = max(midnight - (days - 1) * 86400, end_ts - MAX_SPAN_S + 1)  # DST can move midnight by an hour
    history = _history(ha, ids, start_ts, end_ts)
    return {"role": role_name, "timezone": str(tz), "start": start_ts, "end": end_ts, "as_of": as_of_ts,
            "series": [{"entity_id": eid, "days": series.daily_runtime(history.get(eid, []), start_ts, end_ts, tz)}
                       for eid in ids]}


def _midnight(ts: float, tz) -> float:
    from datetime import datetime
    return datetime.fromtimestamp(ts, tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
