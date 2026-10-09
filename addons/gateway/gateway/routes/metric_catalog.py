from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from gateway.metric_catalog import store
from gateway.metric_catalog.runner import run_from_env

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _conn():
    return store.connect(os.environ.get("CATALOG_DB", "metric_catalog.sqlite3"))


@router.get("/status")
def status():
    conn = _conn()
    try:
        return {"latest_run": store.latest_run_info(conn), "changes": store.diff_runs(conn)}
    finally:
        conn.close()


@router.post("/run")
def run_now():
    """Refresh the catalog now. Read-only toward Home Assistant and VictoriaMetrics."""
    try:
        return run_from_env()
    except Exception as exc:  # surface the cause to whoever is testing the add-on
        raise HTTPException(502, f"{type(exc).__name__}: {exc}") from exc


@router.get("/export.csv")
def export_csv():
    path = Path(os.environ.get("CATALOG_OUT_DIR", "")) / "entity_metrics.csv"
    if not path.is_file():
        raise HTTPException(404, "no export yet; run the catalog first")
    return FileResponse(path, media_type="text/csv")


@router.get("/metrics")
def search(q: str, limit: int = 25):
    conn = _conn()
    try:
        return store.search(conn, q, limit)
    finally:
        conn.close()


@router.get("/metrics/{entity_id}")
def entity(entity_id: str):
    conn = _conn()
    try:
        found = store.get_entity(conn, entity_id)
    finally:
        conn.close()
    if found is None:
        raise HTTPException(404, "entity not in latest catalog run")
    return found
