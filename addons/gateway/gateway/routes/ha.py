"""Read-only Home Assistant endpoints. No route here can change anything in Home Assistant."""

from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, Depends, HTTPException

from gateway.asof import AsOfError, clip_end, iso, parse_time
from gateway.deps import DOMAIN_RE, ENTITY_RE, as_of, entity_ids, get_ha
from gateway.ha_client import HAClient
from gateway.redact import redact

router = APIRouter(prefix="/ha", tags=["home-assistant (read-only)"])

MAX_SPAN_S = 93 * 86400
REGISTRIES = {
    "entity": "config/entity_registry/list",
    "device": "config/device_registry/list",
    "area": "config/area_registry/list",
    "label": "config/label_registry/list",
}


def _live_only(as_of_ts: float | None) -> None:
    if as_of_ts is not None:
        raise HTTPException(409, "live-only endpoint: unavailable while an as-of time is in force; "
                                 "use /ha/history for past states")


def _upstream(fn):
    try:
        return fn()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(exc.response.status_code, "Home Assistant refused the request") from exc
    except (httpx.TransportError, RuntimeError, OSError) as exc:
        raise HTTPException(502, f"Home Assistant unreachable or failed: {exc}") from exc


def _window(start: str | None, end: str | None, as_of_ts: float | None) -> tuple[float, float]:
    try:
        end_ts = clip_end(parse_time(end) if end else None, as_of_ts, time.time())
        start_ts = parse_time(start) if start else end_ts - 86400
    except AsOfError as exc:
        raise HTTPException(400, str(exc)) from exc
    if start_ts >= end_ts:
        raise HTTPException(400, "empty window: start is not before end (after as-of clipping)")
    if end_ts - start_ts > MAX_SPAN_S:
        raise HTTPException(400, "window too long: at most 93 days per request")
    return start_ts, end_ts


@router.get("/states")
def states(domain: str | None = None, entity_id: str | None = None,
           as_of_ts: float | None = Depends(as_of), ha: HAClient = Depends(get_ha)):
    _live_only(as_of_ts)
    rows = _upstream(ha.states)
    if domain:
        if not DOMAIN_RE.match(domain):
            raise HTTPException(400, "bad domain")
        rows = [r for r in rows if r["entity_id"].startswith(domain + ".")]
    if entity_id:
        wanted = set(entity_ids(entity_id, limit=200))
        rows = [r for r in rows if r["entity_id"] in wanted]
    return redact(rows)


@router.get("/states/{entity_id}")
def state(entity_id: str, as_of_ts: float | None = Depends(as_of), ha: HAClient = Depends(get_ha)):
    _live_only(as_of_ts)
    if not ENTITY_RE.match(entity_id):
        raise HTTPException(400, "bad entity id")
    return redact(_upstream(lambda: ha.get(f"/states/{entity_id}")))


@router.get("/history")
def history(entity_id: str, start: str | None = None, end: str | None = None,
            minimal: bool = True, attributes: bool = False,
            as_of_ts: float | None = Depends(as_of), ha: HAClient = Depends(get_ha)):
    ids = entity_ids(entity_id)
    start_ts, end_ts = _window(start, end, as_of_ts)
    params = {"filter_entity_id": ",".join(ids), "end_time": iso(end_ts), "significant_changes_only": "0"}
    if minimal:
        params["minimal_response"] = ""
    if not attributes:
        params["no_attributes"] = ""
    return redact(_upstream(lambda: ha.get(f"/history/period/{iso(start_ts)}", **params)))


@router.get("/logbook")
def logbook(start: str | None = None, end: str | None = None, entity_id: str | None = None,
            as_of_ts: float | None = Depends(as_of), ha: HAClient = Depends(get_ha)):
    start_ts, end_ts = _window(start, end, as_of_ts)
    params = {"end_time": iso(end_ts)}
    if entity_id:
        params["entity"] = entity_ids(entity_id, limit=1)[0]
    return redact(_upstream(lambda: ha.get(f"/logbook/{iso(start_ts)}", **params)))


@router.get("/registry/{kind}")
def registry(kind: str, as_of_ts: float | None = Depends(as_of), ha: HAClient = Depends(get_ha)):
    _live_only(as_of_ts)
    if kind not in REGISTRIES:
        raise HTTPException(404, f"kind must be one of {sorted(REGISTRIES)}")
    return redact(_upstream(lambda: ha.ws_list(REGISTRIES[kind])))


@router.get("/services")
def services(as_of_ts: float | None = Depends(as_of), ha: HAClient = Depends(get_ha)):
    _live_only(as_of_ts)
    return _upstream(lambda: ha.get("/services"))


def _started(trace: dict) -> float:
    return parse_time(trace["timestamp"]["start"])


@router.get("/traces")
def traces(automation_id: str | None = None, limit: int = 20,
           as_of_ts: float | None = Depends(as_of), ha: HAClient = Depends(get_ha)):
    params = {"domain": "automation"}
    if automation_id:
        params["item_id"] = automation_id
    rows = _upstream(lambda: ha.ws_call("trace/list", **params))
    if as_of_ts is not None:
        rows = [r for r in rows if _started(r) <= as_of_ts]
    rows = sorted(rows, key=_started, reverse=True)[: max(1, min(limit, 100))]
    return redact(rows)


@router.get("/traces/{automation_id}/{run_id}")
def trace(automation_id: str, run_id: str, as_of_ts: float | None = Depends(as_of),
          ha: HAClient = Depends(get_ha)):
    body = _upstream(lambda: ha.ws_call("trace/get", domain="automation", item_id=automation_id, run_id=run_id))
    if as_of_ts is not None and _started(body) > as_of_ts:
        raise HTTPException(404, "trace not found")
    return redact(body)
