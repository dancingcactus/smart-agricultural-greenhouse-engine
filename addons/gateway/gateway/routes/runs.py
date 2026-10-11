"""Run ledger: stamps and spend per model role. Stores only the gateway's own bookkeeping."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from gateway import config, runs

router = APIRouter(prefix="/runs", tags=["run ledger"])


class Run(BaseModel):
    role: str = Field(min_length=1, max_length=64)
    chat_id: str | None = None
    message_id: str | None = None
    user_id: str | None = None
    model: str | None = None
    provider: str | None = None
    prompt_hash: str | None = None
    tool_set: str | None = None
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    status: str = "ok"


@router.post("")
def add(run: Run):
    conn = runs.connect(config.runs_db())
    try:
        return {"id": runs.add(conn, run.model_dump(exclude_none=True))}
    finally:
        conn.close()


@router.get("")
def recent(limit: int = 100, role: str | None = None):
    conn = runs.connect(config.runs_db())
    try:
        return runs.recent(conn, max(1, min(limit, 1000)), role)
    finally:
        conn.close()


@router.get("/spend")
def spend(hours: float = 24.0):
    """Calls, tokens and dollars per role over the last `hours` (default: a day)."""
    conn = runs.connect(config.runs_db())
    try:
        return {"hours": hours, "roles": runs.spend(conn, time.time() - hours * 3600)}
    finally:
        conn.close()


@router.get("/daily")
def daily(days: int = 30, offset_minutes: int = 0):
    """Per day and role totals for the last `days` days; offset_minutes is the viewer's offset east of UTC."""
    if not (1 <= days <= 366 and -840 <= offset_minutes <= 840):
        raise HTTPException(400, "days must be 1-366 and offset_minutes within +-840")
    conn = runs.connect(config.runs_db())
    try:
        return runs.daily(conn, days, offset_minutes)
    finally:
        conn.close()
