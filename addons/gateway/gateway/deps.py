"""Shared FastAPI dependencies. Tests replace these with fakes via app.dependency_overrides."""

from __future__ import annotations

import os
import re
from functools import lru_cache

import httpx
from fastapi import Header, HTTPException

from gateway import config
from gateway.asof import AsOfError, effective_as_of
from gateway.ha_client import HAClient


@lru_cache
def get_ha() -> HAClient:
    return HAClient.from_env()


@lru_cache
def get_vm() -> httpx.Client:
    user = os.environ.get("VM_USERNAME", "")
    return httpx.Client(
        base_url=os.environ.get("VM_URL", "http://localhost:8428").rstrip("/"),
        auth=httpx.BasicAuth(user, os.environ.get("VM_PASSWORD", "")) if user else None,
        timeout=60.0,
    )


def as_of(x_as_of: str | None = Header(default=None)) -> float | None:
    """Effective as-of time: the caller's X-As-Of, never later than the gateway-wide override."""
    try:
        return effective_as_of(x_as_of, config.forced_as_of())
    except AsOfError as exc:
        raise HTTPException(400, str(exc)) from exc


ENTITY_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
DOMAIN_RE = re.compile(r"^[a-z0-9_]+$")


def entity_ids(raw: str, limit: int = 50) -> list[str]:
    ids = [x.strip() for x in raw.split(",") if x.strip()]
    if not ids or len(ids) > limit or not all(ENTITY_RE.match(i) for i in ids):
        raise HTTPException(400, f"entity_id must be 1-{limit} comma-separated ids like sensor.name")
    return ids
