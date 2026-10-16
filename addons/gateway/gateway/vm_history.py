"""Raw history from VictoriaMetrics for the forecasting routes. Read-only.

The metric catalog already knows how each entity is stored (series and selectors), so this module only looks a
selector up and exports its samples. Samples are returned as `(epoch_seconds, value)` and go through the same
`series.resample` as Home Assistant history. As-of safety lives in the route: it never asks for a later `end`.
"""

from __future__ import annotations

import json
import math

import httpx

from gateway.metric_catalog import store

CARRY_IN_S = 86400  # also fetch the day before the window: a reading holds until the next, so the last one carries in


class VMError(RuntimeError):
    """VictoriaMetrics could not be read (unreachable, refused, or an unexpected reply)."""


def selectors_for(conn, entity_id: str) -> list[str]:
    """Selectors of the entity's numeric series from the latest catalog run, [] when it is not recorded there.

    An entity can have more than one (for example a label change part way through its history); all are returned.
    """
    ent = store.get_entity(conn, entity_id)
    if ent is None:
        return []
    value = [s["selector"] for s in ent["series"] if s["kind"] == "value"]
    return value or [s["selector"] for s in ent["series"] if s["kind"] == "state"]


def export(vm: httpx.Client, selectors: list[str], start: float, end: float) -> tuple[list[tuple[float, float]], int]:
    """Raw samples of the selected series merged into one time-ordered list, plus how many series were merged.

    Duplicate timestamps keep the later series' value. Non-finite samples are dropped.
    """
    if not selectors:
        return [], 0
    try:
        resp = vm.get("/api/v1/export", params={"match[]": selectors, "start": start - CARRY_IN_S, "end": end})
    except httpx.TransportError as exc:
        raise VMError(f"cannot reach VictoriaMetrics: {exc}") from exc
    if resp.status_code in (401, 403):
        raise VMError("gateway is not authorised to read VictoriaMetrics (vm_username / vm_password)")
    if resp.status_code >= 400:
        raise VMError(f"VictoriaMetrics export failed with HTTP {resp.status_code}")
    merged: dict[float, float] = {}
    lines = 0
    for line in resp.text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            stamps, values = row["timestamps"], row["values"]
        except (ValueError, KeyError, TypeError) as exc:
            raise VMError("VictoriaMetrics export returned something unexpected") from exc
        lines += 1
        for ms, value in zip(stamps, values, strict=False):
            if isinstance(value, (int, float)) and math.isfinite(value):
                merged[ms / 1000.0] = float(value)
    return sorted(merged.items()), lines


def on_off(points: list[tuple[float, float]]) -> list[tuple[float, str]]:
    """Numeric equipment values as the states `series.daily_runtime` counts: 0.5 or more is on."""
    return [(t, "on" if v >= 0.5 else "off") for t, v in points]
