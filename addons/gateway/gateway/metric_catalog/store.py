"""SQLite + FTS5 storage for the entity/metric catalog."""

from __future__ import annotations

import csv
import json
import sqlite3
import time
from pathlib import Path

from .models import EntityRecord, SeriesRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, ingest_mode TEXT,
  n_entities INTEGER, n_series INTEGER, n_missing INTEGER, n_orphans INTEGER);
CREATE TABLE IF NOT EXISTS entities(
  run_id INTEGER, entity_id TEXT, name TEXT, friendly_name TEXT, labels TEXT, icon TEXT,
  platform TEXT, device_id TEXT, area TEXT, unit TEXT, device_class TEXT, state_class TEXT,
  disabled INTEGER, in_registry INTEGER, vm_status TEXT, has_state INTEGER, vm_reason TEXT, mirror_of TEXT, mirrored_by TEXT,
  PRIMARY KEY(run_id, entity_id));
CREATE TABLE IF NOT EXISTS series(
  run_id INTEGER, entity_id TEXT, metric TEXT, labels TEXT, selector TEXT,
  first_seen REAL, last_seen REAL, samples INTEGER, avg_interval_s REAL,
  field TEXT, kind TEXT, history_first_seen REAL);
CREATE INDEX IF NOT EXISTS series_run_entity ON series(run_id, entity_id);
-- Search index holds the latest run only; history stays in the tables above.
CREATE VIRTUAL TABLE IF NOT EXISTS search USING fts5(
  entity_id, name, friendly_name, labels, icon, area, unit, device_class, metrics,
  tokenize='unicode61 remove_diacritics 2 tokenchars ''_.:-''');
"""


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Databases created by earlier versions lack the newer columns.
    _ensure_columns(conn, "entities", {"has_state": "INTEGER", "vm_reason": "TEXT", "mirror_of": "TEXT",
                                      "mirrored_by": "TEXT", "device_name": "TEXT",
                                      "device_model": "TEXT"})
    _ensure_columns(conn, "series", {"field": "TEXT", "kind": "TEXT", "history_first_seen": "REAL"})
    return conn


def _ensure_columns(conn: sqlite3.Connection, table: str, cols: dict[str, str]) -> None:
    have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, typ in cols.items():
        if name not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


def save_run(
    conn: sqlite3.Connection,
    mode: str,
    entities: dict[str, EntityRecord],
    series: list[SeriesRecord],
    ts: float | None = None,
) -> int:
    ents = list(entities.values())
    cur = conn.execute(
        "INSERT INTO runs(ts, ingest_mode, n_entities, n_series, n_missing, n_orphans) "
        "VALUES (?,?,?,?,?,?)",
        (
            ts or time.time(), mode, len(ents), len(series),
            sum(e.vm_status == "missing" for e in ents), sum(e.vm_status == "orphan" for e in ents),
        ),
    )
    run_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO entities(run_id, entity_id, name, friendly_name, labels, icon, platform, "
        "device_id, area, unit, device_class, state_class, disabled, in_registry, vm_status, "
        "has_state, vm_reason, mirror_of, mirrored_by, device_name, device_model) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (run_id, e.entity_id, e.name, e.friendly_name, json.dumps(e.labels), e.icon, e.platform,
             e.device_id, e.area, e.unit, e.device_class, e.state_class, int(e.disabled),
             int(e.in_registry), e.vm_status, int(e.has_state), e.vm_reason, e.mirror_of,
             e.mirrored_by, e.device_name, e.device_model)
            for e in ents
        ],
    )
    conn.executemany(
        "INSERT INTO series(run_id, entity_id, metric, labels, selector, first_seen, last_seen, "
        "samples, avg_interval_s, field, kind, history_first_seen) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (run_id, s.entity_id, s.metric, json.dumps(s.labels, sort_keys=True), s.selector,
             s.first_seen, s.last_seen, s.samples, s.avg_interval_s, s.field, s.kind,
             s.history_first_seen)
            for s in series
        ],
    )
    metrics: dict[str, list[str]] = {}
    for s in series:
        metrics.setdefault(s.entity_id, []).append(s.metric)
    conn.execute("DELETE FROM search")
    conn.executemany(
        "INSERT INTO search VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (e.entity_id, e.name, e.friendly_name, " ".join(e.labels), e.icon, e.area, e.unit,
             e.device_class, " ".join(metrics.get(e.entity_id, [])))
            for e in ents
        ],
    )
    conn.commit()
    return run_id


def latest_run(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(run_id) AS r FROM runs").fetchone()
    return row["r"]


def _fts_query(text: str) -> str:
    # Quote each term so punctuation like "mdi:fan" or "sensor.x" is searched literally; prefix match.
    return " ".join('"' + t.replace('"', '""') + '"*' for t in text.split())


def _entity_row(conn: sqlite3.Connection, run_id: int, eid: str) -> dict:
    ent = dict(conn.execute("SELECT * FROM entities WHERE run_id=? AND entity_id=?", (run_id, eid)).fetchone())
    ent["labels"] = json.loads(ent["labels"])
    ent["series"] = [
        {**dict(r), "labels": json.loads(r["labels"])}
        for r in conn.execute(
            "SELECT metric, field, kind, labels, selector, first_seen, history_first_seen, last_seen, "
            "samples, avg_interval_s FROM series WHERE run_id=? AND entity_id=? "
            "ORDER BY (kind != 'value'), metric", (run_id, eid))
    ]
    return ent


def search(conn: sqlite3.Connection, query: str, limit: int = 25) -> list[dict]:
    run_id = latest_run(conn)
    if run_id is None or not query.strip():
        return []
    rows = conn.execute(
        "SELECT entity_id FROM search WHERE search MATCH ? ORDER BY rank LIMIT ?",
        (_fts_query(query), limit),
    ).fetchall()
    return [_entity_row(conn, run_id, r["entity_id"]) for r in rows]


def get_entity(conn: sqlite3.Connection, entity_id: str) -> dict | None:
    run_id = latest_run(conn)
    if run_id is None:
        return None
    exists = conn.execute(
        "SELECT 1 FROM entities WHERE run_id=? AND entity_id=?", (run_id, entity_id)).fetchone()
    return _entity_row(conn, run_id, entity_id) if exists else None


def diff_runs(conn: sqlite3.Connection) -> dict:
    """Compare the latest run with the one before it."""
    ids = [r["run_id"] for r in conn.execute("SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 2")]
    if len(ids) < 2:
        return {"new": [], "removed": [], "changed": [], "series_appeared": [], "series_gone": []}
    new_id, old_id = ids

    def ents(run_id: int) -> dict[str, sqlite3.Row]:
        return {r["entity_id"]: r for r in conn.execute("SELECT * FROM entities WHERE run_id=?", (run_id,))}

    def sers(run_id: int) -> set[tuple[str, str]]:
        return {(r["entity_id"], r["metric"]) for r in conn.execute(
            "SELECT entity_id, metric FROM series WHERE run_id=?", (run_id,))}

    new, old = ents(new_id), ents(old_id)
    cols = ("name", "friendly_name", "labels", "icon", "area", "unit", "device_class", "vm_status",
            "vm_reason", "mirrored_by")
    changed = []
    for eid in new.keys() & old.keys():
        delta = {c: [old[eid][c], new[eid][c]] for c in cols if old[eid][c] != new[eid][c]}
        if delta:
            changed.append({"entity_id": eid, "changes": delta})
    new_s, old_s = sers(new_id), sers(old_id)
    return {
        "new": sorted(new.keys() - old.keys()),
        "removed": sorted(old.keys() - new.keys()),
        "changed": sorted(changed, key=lambda c: c["entity_id"]),
        "series_appeared": sorted(new_s - old_s),
        "series_gone": sorted(old_s - new_s),
    }


def export(conn: sqlite3.Connection, out_dir: str | Path) -> list[Path]:
    """Write entity_metrics.jsonl (one row per entity) and entity_metrics.csv (one per series)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    run_id = latest_run(conn)
    if run_id is None:
        return []
    ids = [r["entity_id"] for r in conn.execute(
        "SELECT entity_id FROM entities WHERE run_id=? ORDER BY entity_id", (run_id,))]
    jsonl, csv_path = out / "entity_metrics.jsonl", out / "entity_metrics.csv"
    entity_cols = ["entity_id", "name", "friendly_name", "labels", "icon", "area", "unit",
                   "device_class", "disabled", "has_state", "vm_status", "vm_reason", "mirror_of",
                   "mirrored_by", "device_name", "device_model"]
    series_cols = ["metric", "field", "kind", "selector", "first_seen", "history_first_seen",
                   "last_seen", "samples", "avg_interval_s"]
    fields = entity_cols + series_cols
    with jsonl.open("w") as jf, csv_path.open("w", newline="") as cf:
        writer = csv.DictWriter(cf, fieldnames=fields)
        writer.writeheader()
        for eid in ids:
            ent = _entity_row(conn, run_id, eid)
            jf.write(json.dumps(ent, sort_keys=True) + "\n")
            base = {k: ent[k] for k in entity_cols if k != "labels"} | {"labels": ";".join(ent["labels"])}
            for s in ent["series"] or [{}]:
                writer.writerow(base | {k: s.get(k, "") for k in series_cols})
    return [jsonl, csv_path]





def latest_run_info(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def summary(conn: sqlite3.Connection) -> dict:
    """Counts for the latest run: by status, by reason for missing, and by domain."""
    run_id = latest_run(conn)
    if run_id is None:
        return {}
    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    by_domain: dict[str, dict[str, int]] = {}
    for r in conn.execute("SELECT entity_id, vm_status, vm_reason FROM entities WHERE run_id=?", (run_id,)):
        by_status[r["vm_status"]] = by_status.get(r["vm_status"], 0) + 1
        if r["vm_status"] == "missing":
            by_reason[r["vm_reason"] or "unknown"] = by_reason.get(r["vm_reason"] or "unknown", 0) + 1
        dom = by_domain.setdefault(r["entity_id"].split(".", 1)[0], {})
        dom[r["vm_status"]] = dom.get(r["vm_status"], 0) + 1
    orphans = [r["entity_id"] for r in conn.execute(
        "SELECT entity_id FROM entities WHERE run_id=? AND vm_status='orphan' ORDER BY entity_id", (run_id,))]
    return {"by_status": by_status, "missing_by_reason": by_reason, "by_domain": dict(sorted(by_domain.items())),
            "orphans": orphans}


def list_entities(conn: sqlite3.Connection, domain: str | None = None) -> list[dict]:
    """Latest-run entities with the newest time a numeric series was seen, for choosing mirrors."""
    run_id = latest_run(conn)
    if run_id is None:
        return []
    rows = conn.execute(
        "SELECT e.*, (SELECT MAX(s.last_seen) FROM series s WHERE s.run_id=e.run_id AND "
        "s.entity_id=e.entity_id AND s.kind IN ('value','attribute_num')) AS last_value_seen "
        "FROM entities e WHERE e.run_id=? AND (? IS NULL OR e.entity_id LIKE ? || '.%') "
        "ORDER BY e.entity_id", (run_id, domain, domain)).fetchall()
    return [dict(r) for r in rows]
