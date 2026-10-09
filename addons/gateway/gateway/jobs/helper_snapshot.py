"""Re-record rarely-changing helpers straight into VictoriaMetrics.

The InfluxDB integration writes only on change, so a setpoint nobody touches leaves no recent
samples and can age out of retention. This job writes each listed helper's *current* value under
the same series Home Assistant uses, so no extra entities are created and history stays continuous.

This is the gateway's only write path, and it is deliberately narrow: opt-in, restricted to the
helpers matching an explicit allowlist, limited to the domains below, and with no API endpoint
that can trigger it (only the scheduler and startup). Writes go to VictoriaMetrics, never to
Home Assistant.
"""

from __future__ import annotations

import json
import logging
import math
import time
from fnmatch import fnmatchcase

import httpx

from gateway.metric_catalog import store

log = logging.getLogger(__name__)

SUPPORTED_DOMAINS = ("input_number", "input_boolean")
_BOOL = {"on": 1.0, "off": 0.0}


def _esc_measurement(text: str) -> str:
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ")


def _esc_tag(text: str) -> str:
    return _esc_measurement(text).replace("=", "\\=")


def numeric_value(domain: str, state: str) -> float | None:
    if domain == "input_boolean":
        return _BOOL.get(state)
    try:
        value = float(state)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def common_tags(conn, min_share: float = 0.8) -> dict[str, str]:
    """Extra tags (beyond entity_id/domain) that nearly every numeric series carries, e.g. db.

    Home Assistant adds these to every write. Using the shared set, and ignoring one-off tags that
    only an old series has, makes a new series land where Home Assistant's next write will.
    """
    run_id = store.latest_run(conn)
    rows = conn.execute("SELECT labels FROM series WHERE run_id=? AND kind='value'", (run_id,)).fetchall() \
        if run_id is not None else []
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        for key, val in json.loads(row["labels"]).items():
            if key not in ("entity_id", "entity", "domain"):
                counts[(key, val)] = counts.get((key, val), 0) + 1
    return {k: v for (k, v), n in counts.items() if rows and n / len(rows) >= min_share}


def _existing_metrics(conn, entity_id: str) -> set[str]:
    run_id = store.latest_run(conn)
    rows = conn.execute("SELECT metric FROM series WHERE run_id=? AND entity_id=? AND kind='value'",
                        (run_id, entity_id)).fetchall()
    return {r["metric"] for r in rows}


def plan(states: list[dict], patterns: list[str], conn=None, now: float | None = None) -> dict:
    """What would be written: lines, helpers skipped (and why), and how each line was checked.

    Identity follows the InfluxDB integration's convention: the measurement is the unit if there is
    one, else the entity id; tags are domain, entity_id and whatever extra tags the catalog shows
    nearly every series carries. If the catalog already holds a numeric series for the helper and the
    name we would write is not among them, the helper is skipped rather than splitting its history.
    """
    now = int(now or time.time())
    extras = common_tags(conn) if conn is not None else {}
    lines: list[str] = []
    skipped: list[dict] = []
    checks: list[dict] = []
    for st in states:
        eid = st["entity_id"]
        domain, object_id = eid.split(".", 1)
        if domain not in SUPPORTED_DOMAINS or not any(fnmatchcase(eid, p) for p in patterns if p.strip()):
            continue
        value = numeric_value(domain, st.get("state"))
        if value is None:
            skipped.append({"entity_id": eid, "reason": f"state {st.get('state')!r} is not numeric"})
            continue
        unit = (st.get("attributes") or {}).get("unit_of_measurement") or ""
        measurement = unit or eid
        metric = f"{measurement}_value"
        existing = _existing_metrics(conn, eid) if conn is not None else set()
        if existing and metric not in existing:
            skipped.append({"entity_id": eid, "reason": f"would write {metric!r} but the catalog shows "
                            f"{sorted(existing)}; not writing so history is not split"})
            checks.append({"entity_id": eid, "metric": metric, "check": "DIFFERS", "existing": sorted(existing)})
            continue
        checks.append({"entity_id": eid, "metric": metric,
                       "check": "matches existing series" if existing else "new series"})
        tags = {"domain": domain, "entity_id": object_id} | extras
        tag_text = ",".join(f"{_esc_tag(k)}={_esc_tag(v)}" for k, v in sorted(tags.items()))
        lines.append(f"{_esc_measurement(measurement)},{tag_text} value={value!r} {now}")
    return {"lines": lines, "skipped": skipped, "checks": checks, "common_tags": extras,
            "catalog_available": conn is not None and store.latest_run(conn) is not None}


def write_lines(vm: httpx.Client, lines: list[str]) -> None:
    if not lines:
        return
    try:
        resp = vm.post("/write", params={"precision": "s"}, content="\n".join(lines) + "\n")
    except httpx.TransportError as exc:
        raise RuntimeError(f"cannot reach VictoriaMetrics at {vm.base_url}: {exc}") from exc
    if resp.status_code in (401, 403):
        raise RuntimeError("VictoriaMetrics rejected the credentials for writing (vm_username / vm_password)")
    if resp.status_code >= 300:
        raise RuntimeError(f"VictoriaMetrics write failed: HTTP {resp.status_code} {resp.text[:200]}")


def run(ha, vm: httpx.Client, patterns: list[str], db_path: str | None = None, ingest_mode: str | None = None) -> dict:
    """Write current helper values. Only InfluxDB-style ingest is supported."""
    conn = store.connect(db_path) if db_path else None
    try:
        if conn is not None:
            info = store.latest_run_info(conn)
            ingest_mode = ingest_mode or (info or {}).get("ingest_mode")
        if ingest_mode not in (None, "influxdb"):
            raise RuntimeError(f"helper snapshots need InfluxDB-style ingest; catalog says {ingest_mode!r}")
        result = plan(ha.states(), patterns, conn)
        if conn is not None and not result["catalog_available"] and result["lines"]:
            raise RuntimeError("no catalog run yet: run the catalog first so the helper series can be "
                               "written with the same tags Home Assistant uses")
    finally:
        if conn is not None:
            conn.close()
    write_lines(vm, result["lines"])
    return {"written": len(result["lines"]), "skipped": result["skipped"], "common_tags": result["common_tags"]}


def run_from_env() -> dict:
    from gateway import config, deps

    if config.forced_as_of() is not None:
        return {"skipped": "an as-of time is forced; not writing"}
    return run(deps.get_ha(), deps.get_vm(), config.snapshot_patterns(), config.catalog_db())
