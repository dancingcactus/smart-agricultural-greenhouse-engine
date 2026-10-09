"""Archive of weather forecasts as they were issued, so replays see what was known at the time.

Each pull stores the forecast exactly as the weather entity gave it, stamped with the pull time.
Reading "as of" a moment returns the latest forecast pulled at or before it, and never a later one.
Providers refresh less often than we pull, so an unchanged forecast stores a pointer to the
earlier copy instead of repeating its rows.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from gateway.asof import AsOfError, parse_time

FEATURE_BITS = {"daily": 1, "hourly": 2, "twice_daily": 4}  # WeatherEntityFeature
NUMERIC = ("temperature", "templow", "apparent_temperature", "dew_point", "humidity", "pressure",
           "cloud_coverage", "precipitation", "precipitation_probability", "wind_speed", "wind_gust_speed",
           "wind_bearing", "uv_index")
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS pulls(
  pull_id INTEGER PRIMARY KEY AUTOINCREMENT, pulled_at REAL NOT NULL, entity_id TEXT NOT NULL,
  forecast_type TEXT NOT NULL, n_points INTEGER, payload_hash TEXT, same_as INTEGER, units TEXT);
CREATE INDEX IF NOT EXISTS pulls_lookup ON pulls(entity_id, forecast_type, pulled_at);
CREATE TABLE IF NOT EXISTS forecasts(
  pull_id INTEGER NOT NULL, valid_time REAL NOT NULL, condition TEXT, is_daytime INTEGER,
  {", ".join(f"{c} REAL" for c in NUMERIC)}, extra TEXT);
CREATE INDEX IF NOT EXISTS forecasts_pull ON forecasts(pull_id, valid_time);
CREATE TABLE IF NOT EXISTS observations(
  entity_id TEXT NOT NULL, observed_at REAL NOT NULL, condition TEXT, attrs TEXT,
  PRIMARY KEY(entity_id, observed_at));
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def supported_types(attrs: dict) -> list[str]:
    features = attrs.get("supported_features")
    if not isinstance(features, int):
        return ["daily", "hourly"]  # unknown: try the two common ones; failures are reported
    return [t for t, bit in FEATURE_BITS.items() if features & bit]


def _units(attrs: dict) -> dict:
    return {k: v for k, v in attrs.items() if k.endswith("_unit")}


def _points(forecast: list[dict], ftype: str, horizon_hours: int) -> list[dict]:
    points = []
    for item in forecast:
        try:
            when = parse_time(item["datetime"])
        except (KeyError, AsOfError):
            continue
        points.append({**item, "_valid": when})
    points.sort(key=lambda p: p["_valid"])
    if ftype == "hourly" and points:
        # Trim relative to the forecast's own start, not the pull time, so an unchanged provider
        # forecast produces identical points on every pull and can be stored once.
        points = [p for p in points if p["_valid"] <= points[0]["_valid"] + horizon_hours * 3600]
    return points


def _store_points(conn: sqlite3.Connection, pull_id: int, points: list[dict]) -> None:
    cols = ["pull_id", "valid_time", "condition", "is_daytime", *NUMERIC, "extra"]
    rows = []
    for p in points:
        known = {"datetime", "_valid", "condition", "is_daytime", *NUMERIC}
        extra = {k: v for k, v in p.items() if k not in known}
        nums = [p.get(c) if isinstance(p.get(c), (int, float)) else None for c in NUMERIC]
        daytime = p.get("is_daytime")
        rows.append((pull_id, p["_valid"], p.get("condition"), None if daytime is None else int(bool(daytime)),
                     *nums, json.dumps(extra, sort_keys=True) if extra else None))
    conn.executemany(f"INSERT INTO forecasts({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)


def pull(ha, conn: sqlite3.Connection, entity_id: str, now: float | None = None, horizon_hours: int = 72) -> dict:
    """Archive the current observation and every supported forecast type for one weather entity."""
    now = now or time.time()
    state = next((s for s in ha.states() if s["entity_id"] == entity_id), None)
    if state is None:
        raise RuntimeError(f"{entity_id} does not exist in Home Assistant")
    attrs = state.get("attributes") or {}
    conn.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?,?)",
                 (entity_id, now, state.get("state"), json.dumps(attrs, sort_keys=True, default=str)))
    result: dict = {"entity_id": entity_id, "stored": {}, "errors": {}}
    for ftype in supported_types(attrs):
        try:
            event = ha.ws_first_event("weather/subscribe_forecast", entity_id=entity_id, forecast_type=ftype)
            points = _points(event.get("forecast") or [], ftype, horizon_hours)
        except (RuntimeError, OSError, TimeoutError, KeyError, PermissionError) as exc:
            result["errors"][ftype] = f"{type(exc).__name__}: {exc}"
            continue
        digest = hashlib.sha256(json.dumps([{k: v for k, v in p.items() if k != "_valid"} for p in points],
                                           sort_keys=True, default=str).encode()).hexdigest()
        last = conn.execute("SELECT pull_id, payload_hash, same_as FROM pulls WHERE entity_id=? AND forecast_type=? "
                            "ORDER BY pulled_at DESC LIMIT 1", (entity_id, ftype)).fetchone()
        same_as = (last["same_as"] or last["pull_id"]) if last and last["payload_hash"] == digest else None
        cur = conn.execute("INSERT INTO pulls(pulled_at, entity_id, forecast_type, n_points, payload_hash, same_as, units) "
                           "VALUES (?,?,?,?,?,?,?)",
                           (now, entity_id, ftype, len(points), digest, same_as, json.dumps(_units(attrs))))
        if same_as is None:
            _store_points(conn, cur.lastrowid, points)
        result["stored"][ftype] = {"points": len(points), "unchanged": same_as is not None}
    conn.commit()
    if not result["stored"] and result["errors"]:
        raise RuntimeError(f"no forecast could be read for {entity_id}: {result['errors']}")
    return result


def forecast_as_of(conn: sqlite3.Connection, entity_id: str, ftype: str, as_of: float,
                   horizon_hours: int | None = None) -> dict | None:
    """The forecast as it stood at `as_of`: the latest pull at or before it, or None if there was none."""
    row = conn.execute("SELECT * FROM pulls WHERE entity_id=? AND forecast_type=? AND pulled_at<=? "
                       "ORDER BY pulled_at DESC LIMIT 1", (entity_id, ftype, as_of)).fetchone()
    if row is None:
        return None
    limit = row["pulled_at"] + horizon_hours * 3600 if horizon_hours else float("inf")
    points = [dict(r) for r in conn.execute(
        "SELECT * FROM forecasts WHERE pull_id=? AND valid_time<=? ORDER BY valid_time",
        (row["same_as"] or row["pull_id"], limit))]
    for p in points:
        p.pop("pull_id")
        p["extra"] = json.loads(p["extra"]) if p["extra"] else {}
    return {"entity_id": entity_id, "type": ftype, "issued_at": row["pulled_at"], "age_s": as_of - row["pulled_at"],
            "units": json.loads(row["units"] or "{}"), "points": points}


def observations(conn: sqlite3.Connection, entity_id: str, start: float, end: float, limit: int = 1000) -> list[dict]:
    rows = conn.execute("SELECT * FROM observations WHERE entity_id=? AND observed_at>=? AND observed_at<=? "
                        "ORDER BY observed_at DESC LIMIT ?", (entity_id, start, end, limit)).fetchall()
    return [{"observed_at": r["observed_at"], "condition": r["condition"], "attributes": json.loads(r["attrs"])}
            for r in reversed(rows)]


def status(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT entity_id, forecast_type, COUNT(*) AS pulls, MIN(pulled_at) AS first_pull, "
                        "MAX(pulled_at) AS last_pull, SUM(same_as IS NOT NULL) AS unchanged FROM pulls "
                        "GROUP BY entity_id, forecast_type").fetchall()
    obs = conn.execute("SELECT entity_id, COUNT(*) AS n, MIN(observed_at) AS first, MAX(observed_at) AS last "
                       "FROM observations GROUP BY entity_id").fetchall()
    return {"forecasts": [dict(r) for r in rows], "observations": [dict(r) for r in obs]}
