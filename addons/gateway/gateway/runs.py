"""Ledger of model runs: which prompt, model and tools produced each answer, and what it cost.

Open WebUI's filter posts one row per answer. The gateway only stores and summarises them; the
daily caps are enforced by the filter, which reads the totals back before the next request.
"""

from __future__ import annotations

import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    chat_id TEXT, message_id TEXT, user_id TEXT,
    role TEXT NOT NULL, model TEXT, provider TEXT,
    prompt_hash TEXT, tool_set TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER, cost_usd REAL,
    status TEXT NOT NULL DEFAULT 'ok'
);
CREATE INDEX IF NOT EXISTS runs_role_ts ON runs(role, ts);
"""
FIELDS = ("chat_id", "message_id", "user_id", "role", "model", "provider", "prompt_hash", "tool_set",
          "prompt_tokens", "completion_tokens", "cost_usd", "status")


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def add(conn: sqlite3.Connection, row: dict, now: float | None = None) -> int:
    cols = [f for f in FIELDS if f in row]
    cur = conn.execute(
        f"INSERT INTO runs (ts, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))})",
        [now if now is not None else time.time(), *[row[c] for c in cols]],
    )
    conn.commit()
    return cur.lastrowid


def recent(conn: sqlite3.Connection, limit: int = 100, role: str | None = None) -> list[dict]:
    q, args = "SELECT * FROM runs", []
    if role:
        q += " WHERE role = ?"
        args.append(role)
    return [dict(r) for r in conn.execute(q + " ORDER BY ts DESC LIMIT ?", [*args, limit])]


def spend(conn: sqlite3.Connection, since: float) -> dict[str, dict]:
    """Per role since `since`: calls, tokens and dollars. Rows with no cost count as calls, not dollars."""
    rows = conn.execute(
        "SELECT role, COUNT(*) AS calls, COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,"
        " COALESCE(SUM(completion_tokens), 0) AS completion_tokens, COALESCE(SUM(cost_usd), 0) AS cost_usd,"
        " SUM(cost_usd IS NULL) AS unpriced FROM runs WHERE ts >= ? GROUP BY role", (since,))
    return {r["role"]: dict(r) for r in rows}
