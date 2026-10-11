"""The hourly watch: plain-code health and range checks on the live greenhouse. No model is involved.

Everything here is pure: `run` takes already-fetched states, history and roles, so it is easy to test and
cannot change anything. The route in `routes/watch.py` does the fetching.

Checks: missing or stale readings, low sensor batteries, readings that never change, the greenhouse temperature
staying outside the live setpoints (or beyond the alarm limits), and the thermostat reporting a fault. What it does
not check yet: equipment commanded versus observed, and humidity (reported for information, because what the
humidity set point means is not known to the code).
"""

from __future__ import annotations

from gateway.asof import AsOfError, parse_time

MEASUREMENT_SUFFIXES = ("_temperature", "_humidity")
MEASUREMENT_ROLES = ("vpd",)
REQUIRED_ROLES = ("greenhouse_temperature", "greenhouse_humidity", "outside_temperature", "target_temp_low",
                  "target_temp_high", "mode_switch")
BAD_STATES = {"unavailable", "unknown", "none", ""}
THERMOSTAT_FAULTS = {"malfunction": "red", "emergency": "red", "overheat": "red", "misconfiguration": "amber",
                     "temperature_sensor_stalled": "amber", "humidity_sensor_stalled": "amber"}
BATTERY_LOW_PCT = 20.0
SEVERITY = {"green": 0, "amber": 1, "red": 2}


def _ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return parse_time(value)  # RFC3339; a timestamp with no zone is taken as UTC
    except AsOfError:
        return None


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _entities(roles: dict, name: str) -> list[str]:
    return [e["entity_id"] for e in roles.get(name, [])]


def _first(roles: dict, name: str) -> str | None:
    found = _entities(roles, name)
    return found[0] if found else None


def is_measurement(role: str) -> bool:
    return role in MEASUREMENT_ROLES or role.endswith(MEASUREMENT_SUFFIXES)


def run(now: float, roles: dict, states: dict, history: dict, siblings: dict | None = None,
        stale_minutes: float = 30, flat_hours: float = 3, band_minutes: float = 20) -> dict:
    """roles: role -> [{"entity_id"}]; states: entity_id -> HA state dict; history: entity_id -> [(ts, value)]
    (oldest first, including the state at the start of the window); siblings: entity_id -> same-device ids."""
    findings: list[dict] = []
    skipped: list[str] = []
    readings: dict = {}

    def add(severity, check, entity_id, message, **evidence):
        findings.append({"severity": severity, "check": check, "entity_id": entity_id, "message": message,
                         "evidence": evidence})

    gaps = [r for r in REQUIRED_ROLES if r not in roles]
    mode_id = _first(roles, "mode_switch")
    mode = (states.get(mode_id) or {}).get("state") if mode_id else None
    charging = bool(mode) and "charging" in mode.lower()

    # 1. Data health: each measurement role's entities.
    for role in sorted(r for r in roles if is_measurement(r)):
        for eid in _entities(roles, role):
            st = states.get(eid)
            if st is None:
                add("amber", "missing", eid, f"{role}: Home Assistant has no state for {eid}", role=role)
                continue
            value = st.get("state")
            if str(value).lower() in BAD_STATES:
                add("amber", "unavailable", eid, f"{role} reads {value!r}", role=role)
                continue
            updated = max(filter(None, (_ts(st.get("last_reported")), _ts(st.get("last_updated")))), default=None)
            if updated is not None and now - updated > stale_minutes * 60:
                add("amber", "stale", eid, f"{role} has not reported for {round((now - updated) / 60)} minutes",
                    role=role, minutes=round((now - updated) / 60), limit_minutes=stale_minutes)
                continue
            readings[role] = {"entity_id": eid, "value": _num(value), "unit": (st.get("attributes") or {}).get("unit_of_measurement")}
            window = [v for v in _values_since(history.get(eid, []), now - flat_hours * 3600) if _num(v) is not None]
            if window and len({_num(v) for v in window}) == 1 and _covers(history.get(eid, []), now, flat_hours):
                add("amber", "flat", eid, f"{role} has read exactly {window[0]} for {flat_hours:g} hours (possible stuck sensor)",
                    role=role, hours=flat_hours)
            for sib in (siblings or {}).get(eid, []):
                if "battery" in sib:
                    level = _num((states.get(sib) or {}).get("state"))
                    if level is not None and level < BATTERY_LOW_PCT:
                        add("amber", "battery", sib, f"battery for {role} is at {level:g}%", role=role, percent=level)

    # 2. Greenhouse temperature against the live setpoints and alarm limits.
    temp_id = _first(roles, "greenhouse_temperature")
    low_id, high_id = _first(roles, "target_temp_low"), _first(roles, "target_temp_high")
    temp = _num((states.get(temp_id) or {}).get("state")) if temp_id else None
    low = _num((states.get(low_id) or {}).get("state")) if low_id else None
    high = _num((states.get(high_id) or {}).get("state")) if high_id else None
    if charging:
        skipped.append(f"temperature band: the mode is {mode!r}, so the growing band does not apply")
    elif None in (temp, low, high):
        skipped.append("temperature band: needs approved roles greenhouse_temperature, target_temp_low and "
                       "target_temp_high with numeric readings")
    else:
        readings["temperature_band"] = {"low": low, "high": high, "value": temp}
        recent = [_num(v) for v in _values_since(history.get(temp_id, []), now - band_minutes * 60)]
        recent = [v for v in recent if v is not None] or [temp]
        if all(v < low for v in recent):
            add("amber", "below_band", temp_id, f"greenhouse temperature {temp:g} is below the low setpoint {low:g} "
                f"(for at least {band_minutes:g} minutes)", value=temp, setpoint=low)
        elif all(v > high for v in recent):
            add("amber", "above_band", temp_id, f"greenhouse temperature {temp:g} is above the high setpoint {high:g} "
                f"(for at least {band_minutes:g} minutes)", value=temp, setpoint=high)
        cold = _num((states.get(_first(roles, "alarm_cold") or "") or {}).get("state"))
        hot = _num((states.get(_first(roles, "alarm_hot") or "") or {}).get("state"))
        if cold is not None and temp <= cold:
            add("red", "cold_alarm", temp_id, f"greenhouse temperature {temp:g} is at or below the cold alarm limit {cold:g}",
                value=temp, limit=cold)
        if hot is not None and temp >= hot:
            add("red", "hot_alarm", temp_id, f"greenhouse temperature {temp:g} is at or above the hot alarm limit {hot:g}",
                value=temp, limit=hot)

    # 3. Humidity: information only.
    hum_id, hum_target = _first(roles, "greenhouse_humidity"), _first(roles, "target_humidity")
    if hum_id and hum_target:
        readings["humidity"] = {"value": _num((states.get(hum_id) or {}).get("state")),
                                "set_point": _num((states.get(hum_target) or {}).get("state")), "note": "information only"}

    # 4. Thermostat fault reasons.
    for role in ("thermostat_reason", "earth_battery_reason"):
        for eid in _entities(roles, role):
            reason = str((states.get(eid) or {}).get("state", "")).lower()
            if reason in THERMOSTAT_FAULTS:
                add(THERMOSTAT_FAULTS[reason], "thermostat_fault", eid, f"{role} reports {reason!r}", reason=reason)

    skipped.append("equipment commanded versus observed: not implemented yet")
    status = max((f["severity"] for f in findings), key=SEVERITY.get, default="green")
    return {"status": status, "nothing_to_report": not findings, "mode": mode, "findings": findings,
            "readings": readings, "roles_missing": gaps, "skipped": skipped,
            "thresholds": {"stale_minutes": stale_minutes, "flat_hours": flat_hours, "band_minutes": band_minutes}}


def _covers(points: list, now: float, hours: float) -> bool:
    """True if the history reaches back far enough to say anything about the whole window (its first point is
    at or before the window start), so a short history is never reported as flat."""
    return bool(points) and points[0][0] <= now - hours * 3600 + 60


def _values_since(points: list, since: float) -> list:
    """The state in effect at `since` plus every state after it. Home Assistant lists a state only when it changes,
    so a quiet window contains just the earlier point that is still in effect."""
    before = [v for t, v in points if t < since]
    return ([before[-1]] if before else []) + [v for t, v in points if t >= since]
