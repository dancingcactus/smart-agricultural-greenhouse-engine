"""Read access to the mirrored Home Assistant config and its catalogs, as of any time."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Response

from gateway import config
from gateway.deps import as_of
from gateway.snapshot import gitmirror
from gateway.snapshot import run as snapshot_run

router = APIRouter(prefix="/snapshot", tags=["config snapshot"])


def _rev(as_of_ts: float | None) -> str:
    repo = config.mirror_dir()
    rev = gitmirror.commit_at(repo, as_of_ts) if (repo / ".git").exists() else None
    if rev is None:
        raise HTTPException(404, "no config snapshot existed by that time" if as_of_ts is not None
                            else "no config snapshot yet: run one first")
    return rev


def _json_file(rev: str, path: str):
    raw = gitmirror.read_file(config.mirror_dir(), rev, path)
    if raw is None:
        raise HTTPException(404, f"{path} is not in that snapshot")
    return json.loads(raw)


@router.get("/status")
def status():
    repo = config.mirror_dir()
    return {**snapshot_run.read_status(repo), "head": gitmirror.head(repo) if (repo / ".git").exists() else None,
            "snapshots": len(gitmirror.commits(repo, limit=100000)) if (repo / ".git").exists() else 0}


@router.post("/run")
def run_now():
    """Take a snapshot now. Writes only to this add-on's own mirror; never to Home Assistant."""
    try:
        return snapshot_run.run_from_env()
    except snapshot_run.SecretFound as exc:
        raise HTTPException(422, {"error": str(exc), "hits": exc.hits}) from exc
    except Exception as exc:  # surface the cause to whoever is testing the add-on
        raise HTTPException(502, f"{type(exc).__name__}: {exc}") from exc


@router.get("/log")
def log(path: str | None = None, limit: int = 100):
    try:
        return gitmirror.commits(config.mirror_dir(), path, max(1, min(limit, 1000)))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/files")
def files(as_of_ts: float | None = Depends(as_of)):
    rev = _rev(as_of_ts)
    return {"commit": rev, "files": gitmirror.list_files(config.mirror_dir(), rev)}


@router.get("/file")
def file(path: str, as_of_ts: float | None = Depends(as_of)):
    rev = _rev(as_of_ts)
    try:
        raw = gitmirror.read_file(config.mirror_dir(), rev, path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if raw is None:
        raise HTTPException(404, f"{path} is not in that snapshot")
    return Response(raw, media_type="text/plain; charset=utf-8", headers={"X-Snapshot-Commit": rev})


@router.get("/automations")
def automations(managed: bool | None = None, protected: bool | None = None, as_of_ts: float | None = Depends(as_of)):
    rows = _json_file(_rev(as_of_ts), "catalog/automations.json")
    if managed is not None:
        rows = [r for r in rows if r["managed"] is managed]
    if protected is not None:
        rows = [r for r in rows if r["protected"] is protected]
    return rows


@router.get("/automations/{key}")
def automation(key: str, as_of_ts: float | None = Depends(as_of)):
    """Look up by entity id (automation.x), config id, or exact alias."""
    for rec in _json_file(_rev(as_of_ts), "catalog/automations.json"):
        if key in (rec["entity_id"], rec["config_id"], rec["alias"]):
            return rec
    raise HTTPException(404, "no such automation in that snapshot")


@router.get("/entity-usage")
def entity_usage(entity_id: str | None = None, as_of_ts: float | None = Depends(as_of)):
    """Where each entity is used: automations, scripts, scenes and dashboards."""
    usage = _json_file(_rev(as_of_ts), "catalog/entity_usage.json")
    return usage.get(entity_id, {}) if entity_id else usage


@router.get("/entity-references")
def entity_references(entity_id: str | None = None, as_of_ts: float | None = Depends(as_of)):
    """Which automations reference each entity (or one entity)."""
    refs = _json_file(_rev(as_of_ts), "catalog/entity_references.json")
    return refs.get(entity_id, []) if entity_id else refs
