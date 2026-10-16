"""Evenly spaced history for forecasting. Read-only; the forecasts themselves are made on the agent side.

History comes from VictoriaMetrics, which keeps months of it, and falls back to Home Assistant's own history (about
ten days) for an entity that is not recorded there. Every response says which source each series came from.
"""

from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, Depends, HTTPException

from gateway import config, series, vm_history
from gateway.asof import clip_end
from gateway.deps import as_of, entity_ids, get_ha, get_vm
from gateway.glossary import store
from gateway.ha_client import HAClient
from gateway.metric_catalog import store as catalog
from gateway.routes.ha import MAX_SPAN_S, _upstream, _window
from gateway.routes.watch import _history

router = APIRouter(prefix="/history", tags=["history for forecasting"])

MAX_ENTITIES = 5
MAX_SPAN_VM_S = 400 * 86400
SOURCES = ("auto", "vm", "ha")


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


def _check_source(source: str) -> None:
    if source not in SOURCES:
        raise HTTPException(400, f"source must be one of {', '.join(SOURCES)}")


def _fetch(ids: list[str], start_ts: float, end_ts: float, source: str, ha: HAClient, vm: httpx.Client) -> tuple[dict, list]:
    """Raw history per entity: {entity_id: {"points", "source", "merged"}}, plus warnings.

    `end_ts` is already clipped to the as-of time, and it is the only end ever sent upstream.
    """
    out: dict[str, dict] = {}
    warnings: list[str] = []
    todo = list(ids)
    if source != "ha":
        conn = catalog.connect(config.catalog_db())
        try:
            for eid in ids:
                selectors = vm_history.selectors_for(conn, eid)
                if not selectors:
                    continue
                try:
                    points, merged = vm_history.export(vm, selectors, start_ts, end_ts)
                except vm_history.VMError as exc:
                    if source == "vm":
                        raise HTTPException(502, str(exc)) from exc
                    warnings.append(f"{eid}: {exc}; used Home Assistant history instead")
                    continue
                if points:
                    out[eid] = {"points": points, "source": "vm", "merged": merged}
        finally:
            conn.close()
        todo = [e for e in ids if e not in out]
        if source == "vm" and todo:
            raise HTTPException(404, f"not recorded in VictoriaMetrics: {', '.join(todo)}")
    if todo:
        ha_start = max(start_ts, end_ts - MAX_SPAN_S)
        if ha_start > start_ts:
            warnings.append("Home Assistant history was asked for at most 93 days; the window was shortened")
        history = _history(ha, todo, ha_start, end_ts)
        for eid in todo:
            out[eid] = {"points": history.get(eid, []), "source": "ha", "merged": None}
    return out, warnings


def _first_data(points: list) -> float | None:
    return next((t for t, v in points if v is not None), None)


@router.get("/resampled")
def resampled(entity_id: str | None = None, role: str | None = None, step: str = "1h",
              start: str | None = None, end: str | None = None, source: str = "auto",
              as_of_ts: float | None = Depends(as_of), ha: HAClient = Depends(get_ha),
              vm: httpx.Client = Depends(get_vm)):
    """Numeric history as time-weighted means on a fixed grid, [bucket_start_epoch, value] with null for gaps.

    `source`: auto (VictoriaMetrics, falling back to Home Assistant), vm, or ha.
    """
    _check_source(source)
    try:
        seconds = series.step_seconds(step)
    except series.SeriesError as exc:
        raise HTTPException(400, str(exc)) from exc
    ids, role_name = _targets(entity_id, role, as_of_ts)
    start_ts, end_ts = _window(start, end, as_of_ts, MAX_SPAN_VM_S if source != "ha" else MAX_SPAN_S)
    raw, warnings = _fetch(ids, start_ts, end_ts, source, ha, vm)
    try:
        out = []
        for eid in ids:
            points = series.resample(raw[eid]["points"], start_ts, end_ts, seconds)
            out.append({"entity_id": eid, "source": raw[eid]["source"], "series_merged": raw[eid]["merged"],
                        "first_sample": _first_data(points), "points": points})
    except series.SeriesError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"role": role_name, "step_seconds": seconds, "start": start_ts, "end": end_ts, "as_of": as_of_ts,
            "warnings": warnings, "series": out}


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


def _midnight(ts: float, tz) -> float:
    return datetime.fromtimestamp(ts, tz).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


@router.get("/runtime")
def runtime(entity_id: str | None = None, role: str | None = None, days: int = 14, end: str | None = None,
            timezone: str | None = None, source: str = "auto", as_of_ts: float | None = Depends(as_of),
            ha: HAClient = Depends(get_ha), vm: httpx.Client = Depends(get_vm)):
    """Hours switched on per local day (for example the GAHT fan), with run counts. The last day may be partial.

    `source`: auto (VictoriaMetrics, falling back to Home Assistant), vm, or ha.
    """
    _check_source(source)
    cap = MAX_SPAN_VM_S if source != "ha" else MAX_SPAN_S
    if not 1 <= days <= cap // 86400:
        raise HTTPException(400, f"days must be between 1 and {cap // 86400}")
    ids, role_name = _targets(entity_id, role, as_of_ts)
    tz = _tz(ha, timezone)
    end_ts = clip_end(_window(None, end, as_of_ts)[1] if end else None, as_of_ts, time.time())
    start_ts = max(_midnight(end_ts, tz) - (days - 1) * 86400, end_ts - cap + 1)  # DST can move midnight by an hour
    raw, warnings = _fetch(ids, start_ts, end_ts, source, ha, vm)
    out = []
    for eid in ids:
        points = raw[eid]["points"]
        if raw[eid]["source"] == "vm":
            points = vm_history.on_off(points)
        day_rows = series.daily_runtime(points, start_ts, end_ts, tz)
        first = next((d["date"] for d in day_rows if d["known_hours"] > 0), None)
        out.append({"entity_id": eid, "source": raw[eid]["source"], "series_merged": raw[eid]["merged"],
                    "first_sample": first, "days": day_rows})
    return {"role": role_name, "timezone": str(tz), "start": start_ts, "end": end_ts, "as_of": as_of_ts,
            "warnings": warnings, "series": out}
