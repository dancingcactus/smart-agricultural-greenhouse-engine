from __future__ import annotations

import json
import logging

from gateway import config
from gateway.metric_catalog import store as catalog_store
from gateway.snapshot import gitmirror

from . import store

log = logging.getLogger(__name__)


def _references() -> dict:
    """Entity -> automations from the latest config snapshot, if there is one."""
    repo = config.mirror_dir()
    try:
        raw = gitmirror.read_file(repo, "HEAD", "catalog/entity_references.json") if (repo / ".git").exists() else None
        return json.loads(raw) if raw else {}
    except (ValueError, OSError):
        return {}


def sync_from_env() -> dict:
    catalog = catalog_store.connect(config.catalog_db())
    glossary = store.connect(config.glossary_db())
    try:
        return store.sync(catalog, glossary, _references())
    finally:
        catalog.close()
        glossary.close()
