"""
title: SAGE Run Log
author: SAGE
description: Stamps every SAGE answer (role, model, prompt hash, tool set, tokens, cost) in the gateway's run ledger and blocks a role once its daily cap is reached.
version: 0.1.3
"""

import hashlib
import json
import logging
import re

import httpx
from pydantic import BaseModel, Field

ROLE_PREFIX = "sage-"
log = logging.getLogger(__name__)


class SageBlocked(Exception):
    """Raised from the inlet so Open WebUI shows the reason instead of calling the model."""


def role_of(model_id: str | None) -> str | None:
    """'sage-mid' -> 'mid'. Anything that is not a SAGE preset is ignored."""
    if model_id and model_id.startswith(ROLE_PREFIX):
        return model_id[len(ROLE_PREFIX):]
    return None


def prompt_hash(system: str | None) -> str | None:
    """Same hash scripts/build_presets.py puts in the preset description."""
    return hashlib.sha256(system.encode()).hexdigest()[:10] if system else None


def hash_from_description(description: str | None) -> str | None:
    """Open WebUI hides a preset's system prompt from filters, so read the hash the builder wrote
    into the description ("... prompt 0123456789"). It goes stale if the prompt is edited in the UI."""
    found = re.search(r"\bprompt ([0-9a-f]{10})\b", description or "")
    return found.group(1) if found else None


def over_cap(role: str, spend: dict, cost_caps: dict, call_caps: dict) -> str | None:
    """Why this role may not run now, or None. `spend` is the gateway's /runs/spend `roles` object."""
    used = spend.get(role) or {}
    if role in cost_caps and (used.get("cost_usd") or 0) >= cost_caps[role]:
        return f"{role} has spent ${used['cost_usd']:.2f} of its ${cost_caps[role]:.2f} daily cap"
    if role in call_caps and (used.get("calls") or 0) >= call_caps[role]:
        return f"{role} has made {used['calls']} of its {call_caps[role]} daily calls"
    return None


def run_row(role: str, model: dict, message: dict, chat_id, message_id, user_id, tool_set: str) -> dict:
    usage = message.get("usage") or {}
    info = (model or {}).get("info") or {}
    meta = info.get("meta") or {}
    row = {
        "role": role,
        "chat_id": chat_id,
        "message_id": message_id,
        "user_id": user_id,
        "model": info.get("base_model_id") or message.get("model"),
        # OpenRouter reports which host answered; Open WebUI does not always keep it, so this can be empty.
        "provider": message.get("provider") or usage.get("provider"),
        "prompt_hash": hash_from_description(meta.get("description")),
        "tool_set": f"{tool_set}:{','.join(sorted(meta.get('toolIds') or []))}".strip(":") or None,
        "prompt_tokens": usage.get("input_tokens") or usage.get("prompt_tokens"),
        "completion_tokens": usage.get("output_tokens") or usage.get("completion_tokens"),
        "cost_usd": usage.get("cost"),
    }
    return {k: v for k, v in row.items() if v is not None}


async def tell_chat(emitter, message: str) -> None:
    """Show `message` in the chat as the answer's error, plus a toast. Best effort: the block still happens
    (the caller raises) even if this fails, so a stuck-looking chat is the worst case."""
    if not emitter:
        return
    try:
        await emitter({"type": "chat:message:error", "data": {"error": {"content": message}}})
        await emitter({"type": "notification", "data": {"type": "error", "content": message}})
    except Exception:
        log.warning("SAGE run log: could not show the block in the chat", exc_info=True)


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="Filter order")
        gateway_url: str = Field(default="http://CHANGE-ME:8099", description="SAGE Gateway base URL")
        gateway_key: str = Field(default="", description="Gateway API key")
        daily_cost_caps_usd: str = Field(
            default='{"small": 0.5, "mid": 1.0, "builder": 1.0, "reviewer": 1.0}',
            description="JSON: dollars per role per 24 h. A role at or over its cap is blocked. Leave a role out for no cap.",
        )
        daily_call_caps: str = Field(
            default='{"reviewer": 10}',
            description="JSON: calls per role per 24 h (the reviewer is the expensive one).",
        )
        tool_set_version: str = Field(default="", description="Git SHA or label of the tools repo, stamped on each run")
        fail_closed: bool = Field(
            default=False,
            description="Block requests when the gateway cannot be reached. Off by default; the OpenRouter key's credit limit is the hard backstop.",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _headers(self) -> dict:
        return {"X-Gateway-Key": self.valves.gateway_key, "X-Caller": "run-log"}

    async def inlet(self, body: dict, __model__: dict | None = None, __event_emitter__=None) -> dict:
        role = role_of(body.get("model"))
        if not role:
            log.info("SAGE run log: inlet ignored model %r (not a sage-* preset)", body.get("model"))
            return body
        try:
            cost_caps = json.loads(self.valves.daily_cost_caps_usd or "{}")
            call_caps = json.loads(self.valves.daily_call_caps or "{}")
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.valves.gateway_url.rstrip('/')}/runs/spend", headers=self._headers())
                r.raise_for_status()
                spend = r.json().get("roles", {})
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("SAGE run log: could not check the daily cap: %s", exc)
            if self.valves.fail_closed:
                text = f"SAGE run log: cannot check the daily cap ({exc})"
                await tell_chat(__event_emitter__, text)
                raise SageBlocked(text) from exc
            return body
        reason = over_cap(role, spend, cost_caps, call_caps)
        if reason:
            text = f"SAGE daily cap reached: {reason}. Raise it in the SAGE Run Log settings or wait."
            await tell_chat(__event_emitter__, text)
            raise SageBlocked(text)
        body["usage"] = {"include": True}  # ask OpenRouter to report cost with the answer
        return body

    async def outlet(self, body: dict, __model__: dict | None = None, __user__: dict | None = None,
                     __metadata__: dict | None = None) -> dict:
        role = role_of(body.get("model"))
        messages = body.get("messages") or []
        if not role or not messages:
            log.info("SAGE run log: outlet skipped (model=%r, messages=%d)", body.get("model"), len(messages))
            return body
        meta = __metadata__ or {}
        row = run_row(role, __model__ or {}, messages[-1], body.get("chat_id") or meta.get("chat_id"),
                      body.get("id") or meta.get("message_id"), (__user__ or {}).get("id"),
                      self.valves.tool_set_version)
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.post(f"{self.valves.gateway_url.rstrip('/')}/runs", json=row, headers=self._headers())
            if r.status_code >= 400:
                log.warning("SAGE run log: gateway refused the row (%s): %s", r.status_code, r.text[:200])
            else:
                log.info("SAGE run log: recorded %s run", role)
        except httpx.HTTPError:
            log.warning("SAGE run log: could not reach the gateway", exc_info=True)  # never break a chat
        return body
