from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from gateway.metric_catalog import store

router = APIRouter(prefix="/catalog/metrics", tags=["catalog"])


def _conn():
    return store.connect(os.environ.get("CATALOG_DB", "metric_catalog.sqlite3"))


@router.get("")
def search(q: str, limit: int = 25):
    conn = _conn()
    try:
        return store.search(conn, q, limit)
    finally:
        conn.close()


@router.get("/{entity_id}")
def entity(entity_id: str):
    conn = _conn()
    try:
        found = store.get_entity(conn, entity_id)
    finally:
        conn.close()
    if found is None:
        raise HTTPException(404, "entity not in latest catalog run")
    return found
