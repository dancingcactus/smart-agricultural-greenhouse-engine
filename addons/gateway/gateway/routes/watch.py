"""The hourly watch: code-only checks on the live greenhouse. Read-only; no model is involved."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException

from gateway import config, watch
from gateway.asof import iso, parse_time
from gateway.deps import as_of, get_ha
from gateway.glossary import store
from gateway.ha_client import HAClient
from gateway.routes.ha import _live_only, _upstream

router = APIRouter(prefix="/watch", tags=["watch"])


def _history(ha: HAClient, ids: list[str], start: float, end: float) -> dict[str, list[tuple[float, str]]]:
    if not ids:
        return {}
    rows = _upstream(lambda: ha.get(f"/history/period/{iso(start)}", filter_entity_id=",".join(ids),
                                    end_time=iso(end), significant_changes_only="0", minimal_response="", no_attributes=""))
    out: dict[str, list[tuple[float, str]]] = {}
    for series in rows or []:
        if not series:
            continue
        eid = series[0].get("entity_id")  # minimal responses name the entity only on a series' first row
        for item in series:
            ts = parse_time(item.get("last_changed") or item.get("last_updated") or start)
            out.setdefault(eid, []).append((ts, item.get("state")))
    return out


@router.get("")
def run(stale_minutes: float = 30, flat_hours: float = 3, band_minutes: float = 20,
        as_of_ts: float | None = Depends(as_of), ha: HAClient = Depends(get_ha)):
    """Run the checks now. Statuses: green (nothing to report), amber, red."""
    _live_only(as_of_ts)
    if not (1 <= stale_minutes <= 1440 and 0.5 <= flat_hours <= 48 and 1 <= band_minutes <= 360):
        raise HTTPException(400, "threshold out of range")
    conn = store.connect(config.glossary_db())
    try:
        roles = store.roles(conn)
        siblings = {e["entity_id"]: e["same_device"] for e in store.list_entries(conn) if e["same_device"]}
    finally:
        conn.close()
    now = time.time()
    states = {s["entity_id"]: s for s in _upstream(ha.states)}
    hist_ids = sorted({e["entity_id"] for r, es in roles.items() if watch.is_measurement(r) or r == "greenhouse_temperature"
                       for e in es})
    history = _history(ha, hist_ids, now - max(flat_hours * 3600, band_minutes * 60) - 3600, now)
    return watch.run(now, roles, states, history, siblings, stale_minutes, flat_hours, band_minutes)
