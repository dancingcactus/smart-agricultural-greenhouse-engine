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


def series_identity(entity_id: str, unit: str, known: dict | None) -> tuple[str, dict[str, str], str]:
    """(measurement, tags, source) for a helper.

    Prefer the series Home Assistant already wrote, copied exactly. Otherwise follow the
    InfluxDB integration's convention: measurement is the unit if there is one, else the entity id.
    """
    domain, object_id = entity_id.split(".", 1)
    if known:
        return known["metric"][: -len("_value")], dict(known["labels"]), "existing series"
    return unit or entity_id, {"domain": domain, "entity_id": object_id}, "convention"


def _known_series(conn, entity_id: str) -> dict | None:
    if conn is None:
        return None
    entity = store.get_entity(conn, entity_id)
    candidates = [s for s in (entity or {}).get("series", [])
                  if s["kind"] == "value" and s["metric"].endswith("_value")]
    return max(candidates, key=lambda s: s["last_seen"] or 0, default=None)


def plan(states: list[dict], patterns: list[str], conn=None, now: float | None = None) -> dict:
    """What would be written: line-protocol lines plus anything skipped and why."""
    now = int(now or time.time())
    lines: list[str] = []
    skipped: list[dict] = []
    for st in states:
        eid = st["entity_id"]
        domain = eid.split(".", 1)[0]
        if domain not in SUPPORTED_DOMAINS or not any(fnmatchcase(eid, p) for p in patterns if p.strip()):
            continue
        value = numeric_value(domain, st.get("state"))
        if value is None:
            skipped.append({"entity_id": eid, "reason": f"state {st.get('state')!r} is not numeric"})
            continue
        unit = (st.get("attributes") or {}).get("unit_of_measurement") or ""
        measurement, tags, source = series_identity(eid, unit, _known_series(conn, eid))
        tag_text = ",".join(f"{_esc_tag(k)}={_esc_tag(v)}" for k, v in sorted(tags.items()))
        lines.append(f"{_esc_measurement(measurement)},{tag_text} value={value!r} {now}")
        log.debug("%s -> %s (%s)", eid, measurement, source)
    return {"lines": lines, "skipped": skipped}


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
    finally:
        if conn is not None:
            conn.close()
    write_lines(vm, result["lines"])
    return {"written": len(result["lines"]), "skipped": result["skipped"]}


def run_from_env() -> dict:
    from gateway import config, deps

    if config.forced_as_of() is not None:
        return {"skipped": "an as-of time is forced; not writing"}
    return run(deps.get_ha(), deps.get_vm(), config.snapshot_patterns(), config.catalog_db())
