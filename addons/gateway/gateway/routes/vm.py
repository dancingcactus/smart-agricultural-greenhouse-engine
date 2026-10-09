"""Read-only VictoriaMetrics proxy. Every query is clipped to the as-of time."""

from __future__ import annotations

import re
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from gateway.asof import AsOfError, check_promql, clip_end, parse_time
from gateway.deps import as_of, get_vm

router = APIRouter(prefix="/vm", tags=["victoriametrics (read-only)"])

STEP_RE = re.compile(r"^\d+(?:\.\d+)?(?:ms|s|m|h|d|w)?$")
LABEL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DEFAULT_SERIES_WINDOW_S = 7 * 86400


def _bad(exc: AsOfError) -> HTTPException:
    return HTTPException(400, str(exc))


def _forward(vm: httpx.Client, path: str, params: dict) -> Response:
    try:
        resp = vm.get(path, params=params)
    except httpx.TransportError as exc:
        raise HTTPException(502, f"cannot reach VictoriaMetrics: {exc}") from exc
    if resp.status_code in (401, 403):
        raise HTTPException(502, "gateway is not authorised to read VictoriaMetrics (vm_username / vm_password)")
    return Response(resp.content, status_code=resp.status_code, media_type="application/json")


def _check_queries(queries: list[str]) -> None:
    # Enforced always, not only under an as-of time, so a tool written live also works in a replay.
    try:
        for q in queries:
            check_promql(q)
    except AsOfError as exc:
        raise _bad(exc) from exc


def _end(end: str | None, as_of_ts: float | None) -> float:
    try:
        return clip_end(parse_time(end) if end else None, as_of_ts, time.time())
    except AsOfError as exc:
        raise _bad(exc) from exc


def _start(start: str | None, end_ts: float, default_span: float) -> float:
    try:
        start_ts = parse_time(start) if start else end_ts - default_span
    except AsOfError as exc:
        raise _bad(exc) from exc
    if start_ts >= end_ts:
        raise HTTPException(400, "empty window: start is not before end (after as-of clipping)")
    return start_ts


@router.get("/query")
def query(query: str, at: str | None = Query(None, alias="time"),
          as_of_ts: float | None = Depends(as_of), vm: httpx.Client = Depends(get_vm)):
    _check_queries([query])
    return _forward(vm, "/api/v1/query", {"query": query, "time": _end(at, as_of_ts)})


@router.get("/query_range")
def query_range(query: str, start: str, step: str = "60s", end: str | None = None,
                as_of_ts: float | None = Depends(as_of), vm: httpx.Client = Depends(get_vm)):
    _check_queries([query])
    if not STEP_RE.match(step):
        raise HTTPException(400, "step must be a number or a duration such as 60s")
    end_ts = _end(end, as_of_ts)
    start_ts = _start(start, end_ts, 0)
    return _forward(vm, "/api/v1/query_range",
                    {"query": query, "start": start_ts, "end": end_ts, "step": step})


@router.get("/series")
def series(match: list[str] = Query(alias="match[]"), start: str | None = None, end: str | None = None,
           as_of_ts: float | None = Depends(as_of), vm: httpx.Client = Depends(get_vm)):
    _check_queries(match)
    end_ts = _end(end, as_of_ts)
    start_ts = _start(start, end_ts, DEFAULT_SERIES_WINDOW_S)
    return _forward(vm, "/api/v1/series", {"match[]": match, "start": start_ts, "end": end_ts})


@router.get("/labels")
def labels(start: str | None = None, end: str | None = None,
           as_of_ts: float | None = Depends(as_of), vm: httpx.Client = Depends(get_vm)):
    end_ts = _end(end, as_of_ts)
    return _forward(vm, "/api/v1/labels",
                    {"start": _start(start, end_ts, DEFAULT_SERIES_WINDOW_S), "end": end_ts})


@router.get("/label/{name}/values")
def label_values(name: str, start: str | None = None, end: str | None = None,
                 as_of_ts: float | None = Depends(as_of), vm: httpx.Client = Depends(get_vm)):
    if not LABEL_RE.match(name):
        raise HTTPException(400, "bad label name")
    end_ts = _end(end, as_of_ts)
    return _forward(vm, f"/api/v1/label/{name}/values",
                    {"start": _start(start, end_ts, DEFAULT_SERIES_WINDOW_S), "end": end_ts})
