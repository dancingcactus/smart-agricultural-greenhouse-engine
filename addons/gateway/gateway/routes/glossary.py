"""The entity glossary: readable by the agent side, approvable only by an owner."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from gateway import config
from gateway.deps import as_of
from gateway.glossary import store

router = APIRouter(prefix="/glossary", tags=["glossary"])


class Draft(BaseModel):
    entity_id: str
    meaning: str = Field(max_length=store.MAX_MEANING)
    aliases: list[str] | None = None


class OwnerEdit(BaseModel):
    meaning: str | None = Field(default=None, max_length=store.MAX_MEANING)
    aliases: list[str] | None = None
    status: Literal["draft", "approved", "rejected"] | None = None


def _conn():
    return store.connect(config.glossary_db())


def _http(exc: store.GlossaryError) -> HTTPException:
    return HTTPException(exc.code, str(exc))


@router.get("/whoami")
def whoami(request: Request):
    """Lets the review page tell whether it was opened through Home Assistant (can approve) or not."""
    return {"owner": request.state.owner, "user": _user(request) if request.state.owner else None}


def _user(request: Request) -> str:
    return (request.headers.get("x-remote-user-display-name") or request.headers.get("x-remote-user-name")
            or "owner")


@router.get("")
def list_entries(status: str | None = None, q: str | None = None, include_inactive: bool = False,
                 as_of_ts: float | None = Depends(as_of)):
    """Live: every entry with its status. With an as-of time: only what was approved by then."""
    conn = _conn()
    try:
        if as_of_ts is not None:
            rows = store.approved_as_of(conn, as_of_ts)
            if q:
                rows = [r for r in rows if q.lower() in f"{r['entity_id']} {r['meaning']} {' '.join(r['aliases'])}".lower()]
            return rows
        return store.list_entries(conn, status, q, active_only=not include_inactive)
    finally:
        conn.close()


@router.get("/{entity_id}")
def get_entry(entity_id: str, as_of_ts: float | None = Depends(as_of)):
    conn = _conn()
    try:
        if as_of_ts is not None:
            found = [r for r in store.approved_as_of(conn, as_of_ts) if r["entity_id"] == entity_id]
            if not found:
                raise HTTPException(404, "no approved entry as of that time")
            return found[0]
        try:
            return store.get(conn, entity_id)
        except store.GlossaryError as exc:
            raise _http(exc) from exc
    finally:
        conn.close()


@router.post("/drafts")
def propose(draft: Draft):
    """Suggest a meaning. Allowed with the API key, but it only ever writes a draft."""
    conn = _conn()
    try:
        return store.propose(conn, draft.entity_id, draft.meaning, draft.aliases)
    except store.GlossaryError as exc:
        raise _http(exc) from exc
    finally:
        conn.close()


@router.put("/{entity_id}")
def owner_edit(entity_id: str, edit: OwnerEdit, request: Request):
    """Approve, reject or edit an entry. The middleware already limits this to owners; this checks again."""
    if not request.state.owner:
        raise HTTPException(403, "owner only")
    conn = _conn()
    try:
        return store.owner_update(conn, entity_id, _user(request), edit.meaning, edit.aliases, edit.status)
    except store.GlossaryError as exc:
        raise _http(exc) from exc
    finally:
        conn.close()
