"""The glossary: plain-English meanings for entities, drafted automatically and approved by an owner.

Drafts can come from the catalog or from the agent; only an owner (see middleware) can approve.
Every change appends a version row, so a replay can read the glossary as it stood at a past time.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time

STATUSES = ("draft", "approved", "rejected")
MAX_MEANING = 600
# Domains whose entities are worth explaining. Automations carry their own descriptions.
DOMAINS = ("sensor", "binary_sensor", "input_number", "input_boolean", "input_select", "input_datetime",
           "switch", "light", "climate", "cover", "fan", "number", "select", "weather", "humidifier", "valve")
SCHEMA = """
CREATE TABLE IF NOT EXISTS entries(
  entity_id TEXT PRIMARY KEY, friendly_name TEXT, unit TEXT, area TEXT, device_class TEXT, vm_status TEXT,
  active INTEGER, cryptic REAL, referenced_by TEXT, meaning TEXT, aliases TEXT, status TEXT, source TEXT,
  drafted_at REAL, updated_at REAL, updated_by TEXT, approved_at REAL, approved_by TEXT);
CREATE TABLE IF NOT EXISTS versions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, entity_id TEXT, ts REAL, meaning TEXT, aliases TEXT,
  status TEXT, by TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS versions_lookup ON versions(entity_id, ts);
"""


class GlossaryError(ValueError):
    """Maps to an HTTP error: .code is the status."""

    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def cryptic_score(entity_id: str, friendly_name: str) -> float:
    """0 (self-explanatory) to 1 (cryptic): hex-looking or numbered ids, and names that say more than the id."""
    id_tokens = _tokens(entity_id.split(".", 1)[1])
    score = 0.0
    if any(re.fullmatch(r"(?=.*\d)[0-9a-f]{4,}", t) for t in id_tokens):
        score += 0.5  # e.g. a device suffix like "0cc7"
    if id_tokens and re.fullmatch(r"\d+", id_tokens[-1]):
        score += 0.2  # e.g. "_2"
    name_tokens = _tokens(friendly_name or "")
    if name_tokens:
        score += 0.3 * sum(t not in id_tokens for t in name_tokens) / len(name_tokens)
    return round(min(score, 1.0), 2)


def draft_text(row: dict) -> str:
    """A factual first draft built only from what the catalog knows; the owner supplies the meaning."""
    name = row.get("friendly_name") or row["entity_id"]
    detail = [d for d in (
        (row.get("device_class") or "").replace("_", " "),
        f"in {row['unit']}" if row.get("unit") else "",
        f"located in {row['area']}" if row.get("area") else "",
    ) if d]
    sentence = " ".join(detail)
    # Upper-case only the first letter: str.capitalize() would lower-case units and area names.
    return f"{name}." + (f" {sentence[:1].upper()}{sentence[1:]}." if detail else "")


def sync(catalog: sqlite3.Connection, glossary: sqlite3.Connection, references: dict | None = None,
         now: float | None = None) -> dict:
    """Add a draft for each eligible entity in the latest catalog run; refresh facts, never owner text."""
    now = now or time.time()
    run = catalog.execute("SELECT MAX(run_id) AS r FROM runs").fetchone()["r"]
    if run is None:
        return {"added": 0, "updated": 0, "deactivated": 0}
    eligible = {}
    for row in catalog.execute("SELECT * FROM entities WHERE run_id=?", (run,)):
        domain = row["entity_id"].split(".", 1)[0]
        if domain in DOMAINS and not row["disabled"] and row["vm_status"] in ("ok", "mirrored", "string_only"):
            eligible[row["entity_id"]] = dict(row)
    existing = {r["entity_id"] for r in glossary.execute("SELECT entity_id FROM entries")}
    added = updated = 0
    for eid, row in eligible.items():
        refs = json.dumps((references or {}).get(eid, []))
        facts = (row["friendly_name"] or row["name"] or "", row["unit"] or "", row["area"] or "",
                 row["device_class"] or "", row["vm_status"], cryptic_score(eid, row["friendly_name"] or row["name"] or ""))
        if eid in existing:
            glossary.execute("UPDATE entries SET friendly_name=?, unit=?, area=?, device_class=?, vm_status=?, cryptic=?, "
                             "active=1, referenced_by=? WHERE entity_id=?", (*facts, refs, eid))
            updated += 1
        else:
            text = draft_text({**row, "friendly_name": facts[0]})
            glossary.execute("INSERT INTO entries(entity_id, friendly_name, unit, area, device_class, vm_status, cryptic, "
                             "active, referenced_by, meaning, aliases, status, source, drafted_at, updated_at) "
                             "VALUES (?,?,?,?,?,?,?,1,?,?,?,?,?,?,?)",
                             (eid, *facts, refs, text, "[]", "draft", "generated", now, now))
            glossary.execute("INSERT INTO versions(entity_id, ts, meaning, aliases, status, by, source) VALUES (?,?,?,?,?,?,?)",
                             (eid, now, text, "[]", "draft", "catalog", "generated"))
            added += 1
    gone = existing - set(eligible)
    for eid in gone:
        glossary.execute("UPDATE entries SET active=0 WHERE entity_id=?", (eid,))
    glossary.commit()
    return {"added": added, "updated": updated, "deactivated": len(gone)}


def _clean(meaning: str | None, aliases: list[str] | None) -> tuple[str | None, list[str] | None]:
    if meaning is not None:
        meaning = meaning.strip()
        if len(meaning) > MAX_MEANING:
            raise GlossaryError(f"meaning is longer than {MAX_MEANING} characters")
    if aliases is not None:
        aliases = [a.strip() for a in aliases if a.strip()][:20]
        if any(len(a) > 80 for a in aliases):
            raise GlossaryError("an alias is longer than 80 characters")
    return meaning, aliases


def _record(glossary, eid, now, row, by, source):
    glossary.execute("INSERT INTO versions(entity_id, ts, meaning, aliases, status, by, source) VALUES (?,?,?,?,?,?,?)",
                     (eid, now, row["meaning"], row["aliases"], row["status"], by, source))


def _get(glossary, eid):
    row = glossary.execute("SELECT * FROM entries WHERE entity_id=?", (eid,)).fetchone()
    if row is None:
        raise GlossaryError(f"{eid} is not in the glossary (it must be a recorded entity in the catalog)", 404)
    return row


def propose(glossary, entity_id: str, meaning: str, aliases: list[str] | None = None, source: str = "agent",
            now: float | None = None) -> dict:
    """A draft from the agent side. Never approves, and never touches an approved entry."""
    now = now or time.time()
    row = _get(glossary, entity_id)
    if row["status"] == "approved":
        raise GlossaryError(f"{entity_id} is already approved; only an owner can change it", 409)
    meaning, aliases = _clean(meaning, aliases)
    if not meaning:
        raise GlossaryError("meaning must not be empty")
    glossary.execute("UPDATE entries SET meaning=?, aliases=?, source=?, updated_at=?, updated_by=? WHERE entity_id=?",
                     (meaning, json.dumps(aliases if aliases is not None else json.loads(row["aliases"])), source, now,
                      source, entity_id))
    _record(glossary, entity_id, now, _get(glossary, entity_id), source, source)
    glossary.commit()
    return get(glossary, entity_id)


def owner_update(glossary, entity_id: str, by: str, meaning: str | None = None, aliases: list[str] | None = None,
                 status: str | None = None, now: float | None = None) -> dict:
    now = now or time.time()
    row = _get(glossary, entity_id)
    meaning, aliases = _clean(meaning, aliases)
    if status is not None and status not in STATUSES:
        raise GlossaryError(f"status must be one of {STATUSES}")
    new_meaning = row["meaning"] if meaning is None else meaning
    new_status = status or row["status"]
    if new_status == "approved" and not new_meaning:
        raise GlossaryError("cannot approve an empty meaning")
    approved = new_status == "approved"
    glossary.execute(
        "UPDATE entries SET meaning=?, aliases=?, status=?, source='owner', updated_at=?, updated_by=?, "
        "approved_at=?, approved_by=? WHERE entity_id=?",
        (new_meaning, json.dumps(aliases) if aliases is not None else row["aliases"], new_status, now, by,
         now if approved else None, by if approved else None, entity_id))
    _record(glossary, entity_id, now, _get(glossary, entity_id), by, "owner")
    glossary.commit()
    return get(glossary, entity_id)


def _present(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["aliases"] = json.loads(out["aliases"] or "[]")
    out["referenced_by"] = json.loads(out["referenced_by"] or "[]")
    out["active"] = bool(out["active"])
    return out


def get(glossary, entity_id: str) -> dict:
    return _present(_get(glossary, entity_id))


def list_entries(glossary, status: str | None = None, q: str | None = None, active_only: bool = True) -> list[dict]:
    rows = glossary.execute("SELECT * FROM entries ORDER BY cryptic DESC, entity_id").fetchall()
    out = [_present(r) for r in rows]
    if active_only:
        out = [e for e in out if e["active"]]
    if status:
        out = [e for e in out if e["status"] == status]
    if q:
        needle = q.lower()
        out = [e for e in out if needle in " ".join([e["entity_id"], e["friendly_name"] or "", e["meaning"] or "",
                                                     " ".join(e["aliases"])]).lower()]
    return out


def approved_as_of(glossary, as_of: float) -> list[dict]:
    """Entries as the owners had approved them at `as_of`; drafts and later edits are invisible."""
    rows = glossary.execute(
        "SELECT v.entity_id, v.meaning, v.aliases, v.ts, v.by FROM versions v JOIN "
        "(SELECT entity_id, MAX(id) AS mid FROM versions WHERE ts<=? GROUP BY entity_id) m ON v.id=m.mid "
        "WHERE v.status='approved' ORDER BY v.entity_id", (as_of,)).fetchall()
    return [{"entity_id": r["entity_id"], "meaning": r["meaning"], "aliases": json.loads(r["aliases"] or "[]"),
             "approved_at": r["ts"], "approved_by": r["by"], "status": "approved"} for r in rows]
