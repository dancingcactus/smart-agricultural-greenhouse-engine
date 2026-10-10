from __future__ import annotations

import json
import logging

from gateway import config
from gateway.metric_catalog import store as catalog_store
from gateway.snapshot import gitmirror

from . import store

log = logging.getLogger(__name__)


def _read_json(path: str):
    repo = config.mirror_dir()
    try:
        raw = gitmirror.read_file(repo, "HEAD", path) if (repo / ".git").exists() else None
        return json.loads(raw) if raw else None
    except (ValueError, OSError):
        return None


def _usage() -> dict:
    """Entity -> where it is used, from the latest config snapshot (empty until one has been taken)."""
    usage = _read_json("catalog/entity_usage.json")
    if usage is not None:
        return usage
    # Snapshots taken before usage was recorded only know which automations use each entity.
    old = _read_json("catalog/entity_references.json") or {}
    return {entity: {"automations": [{"id": a, "name": a} for a in autos]} for entity, autos in old.items()}


def sync_from_env() -> dict:
    catalog = catalog_store.connect(config.catalog_db())
    glossary = store.connect(config.glossary_db())
    try:
        return store.sync(catalog, glossary, _usage())
    finally:
        catalog.close()
        glossary.close()
