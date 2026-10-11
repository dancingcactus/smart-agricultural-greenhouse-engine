"""The daily review's numbers: plain-code summary of the last day and the next three, so the model reads results
instead of doing arithmetic. Nothing here changes anything.

Like `watch`, everything is pure: `daily` takes already-fetched states, history and forecast, and the route in
`routes/review.py` does the fetching. Checks it adds on top of the watch: time in the temperature band, night
humidity, equipment runtime, and frost, heat or wind in the forecast.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from gateway import watch

# Which loops the mode allows. Keys are matched against the mode switch's state, lower-cased.
CADENCE = {
    "growing": ("watch", "daily", "evening", "weekly"),
    "charging": ("daily", "weekly"),  # provisional: charging checks replace the crop checks
    "off": ("weekly",),
}
DEFAULT_MODE = "growing"
ON_STATES = {"on", "open", "opening", "heat", "heating", "cool", "cooling", "running", "fan_only"}

HUMIDITY_RISK_PCT = 85.0
NIGHT_START_HOUR, NIGHT_END_HOUR = 20, 6
MIN_TIME_IN_BAND_PCT = 80.0
NIGHT_RISK_HOURS = 2.0
FROST_F, HEAT_F, WIND_MPH = 34.0, 90.0, 30.0
FORECAST_HOURS = 72
NOT_MEASURED = ["light", "soil moisture", "basket weight", "ground temperature", "pH/EC", "fertilizer level",
                "camera images"]


def loops_due(mode: str | None) -> dict:
    """Which loops may run in this mode. An unrecognised mode falls back to the growing cadence and says so."""
    text = (mode or "").lower()
    key = next((k for k in CADENCE if k in text), None)
    return {"mode": mode, "cadence": key or DEFAULT_MODE, "recognised": key is not None,
            "loops": list(CADENCE[key or DEFAULT_MODE])}


def _tz(name: str | None):
    try:
        return ZoneInfo(name) if name else UTC
    except ZoneInfoNotFoundError:
        return UTC


def _segments(points: list, start: float, end: float) -> list[tuple[float, float, str]]:
    """Step-function history as (from, to, state) pieces inside [start, end]; each state holds until the next."""
    pts = sorted(points)
    out = []
    for i, (t, v) in enumerate(pts):
        nxt = pts[i + 1][0] if i + 1 < len(pts) else end
        a, b = max(t, start), min(nxt, end)
        if b > a:
            out.append((a, b, v))
    return out


def _is_night(ts: float, tz) -> bool:
    hour = datetime.fromtimestamp(ts, tz).hour
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR


def _split_night(a: float, b: float, tz) -> float:
    """Seconds of [a, b] that fall at night, checked in 10-minute steps (precise enough for hours-long totals)."""
    total, t = 0.0, a
    while t < b:
        step = min(600.0, b - t)
        if _is_night(t + step / 2, tz):
            total += step
        t += step
    return total


def temperature_summary(points: list, now: float, hours: float, low: float | None, high: float | None) -> dict | None:
    segs = [(a, b, float(v)) for a, b, v in _segments(points, now - hours * 3600, now) if _isnum(v)]
    if not segs:
        return None
    covered = sum(b - a for a, b, _ in segs)
    values = [v for _, _, v in segs]
    out = {"min": min(values), "max": max(values), "mean": round(sum((b - a) * v for a, b, v in segs) / covered, 1),
           "covered_hours": round(covered / 3600, 1)}
    if low is not None and high is not None:
        inside = sum(b - a for a, b, v in segs if low <= v <= high)
        out.update(band={"low": low, "high": high}, pct_in_band=round(100 * inside / covered, 1),
                   hours_below=round(sum(b - a for a, b, v in segs if v < low) / 3600, 1),
                   hours_above=round(sum(b - a for a, b, v in segs if v > high) / 3600, 1),
                   note="measured against today's setpoints, not the setpoints in force at each moment")
    return out


def humidity_summary(points: list, now: float, hours: float, tz) -> dict | None:
    segs = [(a, b, float(v)) for a, b, v in _segments(points, now - hours * 3600, now) if _isnum(v)]
    if not segs:
        return None
    risky = [(a, b) for a, b, v in segs if v >= HUMIDITY_RISK_PCT]
    night = sum(_split_night(a, b, tz) for a, b in risky)
    values = [v for _, _, v in segs]
    return {"min": min(values), "max": max(values), "hours_at_or_above_risk": round(sum(b - a for a, b in risky) / 3600, 1),
            "night_hours_at_or_above_risk": round(night / 3600, 1), "risk_pct": HUMIDITY_RISK_PCT,
            "night_window": f"{NIGHT_START_HOUR}:00-{NIGHT_END_HOUR}:00 local"}


def equipment_summary(points: list, now: float, hours: float) -> dict:
    """Runtime, starts and longest run inside the window for one piece of equipment."""
    runs: list[list[float]] = []
    for a, b, v in _segments(points, now - hours * 3600, now):
        if str(v).lower() not in ON_STATES:
            continue
        if runs and runs[-1][1] == a:
            runs[-1][1] = b  # contiguous "on" states (for example heat then cool) are one run
        else:
            runs.append([a, b])
    return {"runtime_hours": round(sum(b - a for a, b in runs) / 3600, 2), "runs": len(runs),
            "longest_run_minutes": round(max((b - a for a, b in runs), default=0) / 60)}


def forecast_summary(forecast: dict | None, now: float) -> dict:
    """Frost, heat and wind in the next 72 hours, from the forecast archive (converted to F and mph)."""
    if not forecast:
        return {"available": False, "note": "no forecast archived yet (set weather_entities)"}
    units = forecast.get("units") or {}
    t_unit, w_unit = str(units.get("temperature_unit", "°F")), str(units.get("wind_speed_unit", "mph"))
    to_f = (lambda c: c * 9 / 5 + 32) if "C" in t_unit else (lambda f: f)
    to_mph = {"km/h": 0.621371, "m/s": 2.23694, "kn": 1.15078}.get(w_unit, 1.0)
    end = now + FORECAST_HOURS * 3600
    pts = [p for p in forecast.get("points", []) if now <= p["valid_time"] <= end]
    temps = [(to_f(p["temperature"]), p["valid_time"]) for p in pts if p.get("temperature") is not None]
    lows = [(to_f(p["templow"]), p["valid_time"]) for p in pts if p.get("templow") is not None]
    winds = [(max(p.get("wind_gust_speed") or 0, p.get("wind_speed") or 0) * to_mph, p["valid_time"]) for p in pts
             if p.get("wind_gust_speed") is not None or p.get("wind_speed") is not None]
    coldest = min(temps + lows, default=None)
    hottest = max(temps, default=None)
    windiest = max(winds, default=None)
    out = {"available": True, "issued_hours_ago": round((now - forecast["issued_at"]) / 3600, 1),
           "points": len(pts), "units_shown": "°F, mph"}
    if coldest:
        out["coldest"] = {"temperature_f": round(coldest[0], 1), "at": coldest[1]}
    if hottest:
        out["hottest"] = {"temperature_f": round(hottest[0], 1), "at": hottest[1]}
    if windiest:
        out["windiest"] = {"wind_mph": round(windiest[0], 1), "at": windiest[1]}
    return out


def _isnum(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def daily(now: float, roles: dict, states: dict, history: dict, watch_result: dict, forecast: dict | None = None,
          time_zone: str | None = None, hours: float = 24) -> dict:
    tz = _tz(time_zone)
    findings: list[dict] = []

    def add(severity, check, message, **evidence):
        findings.append({"severity": severity, "check": check, "message": message, "evidence": evidence})

    for f in watch_result.get("findings", []):  # the watch's data-health and alarm findings lead the report
        findings.append({**f, "source": "watch"})

    def live(name):
        eid = watch._first(roles, name)
        return eid, watch._num((states.get(eid) or {}).get("state")) if eid else None

    temp_id, _ = live("greenhouse_temperature")
    _, low = live("target_temp_low")
    _, high = live("target_temp_high")
    hum_id, _ = live("greenhouse_humidity")
    out_id, _ = live("outside_temperature")
    mode = watch_result.get("mode")
    cadence = loops_due(mode)
    charging = cadence["cadence"] == "charging"

    temperature = temperature_summary(history.get(temp_id, []), now, hours, None if charging else low,
                                      None if charging else high) if temp_id else None
    outside = temperature_summary(history.get(out_id, []), now, hours, None, None) if out_id else None
    humidity = humidity_summary(history.get(hum_id, []), now, hours, tz) if hum_id else None
    equipment = {role: {"entity_id": eid, **equipment_summary(history.get(eid, []), now, hours)}
                 for role in sorted(r for r in roles if r.startswith("equipment_"))
                 for eid in watch._entities(roles, role)[:1]}
    forecast_out = forecast_summary(forecast, now)

    if temperature and temperature.get("pct_in_band") is not None and temperature["pct_in_band"] < MIN_TIME_IN_BAND_PCT:
        add("amber", "time_in_band", f"greenhouse temperature was in the band {temperature['pct_in_band']:g}% of the last "
            f"{hours:g} hours (below {MIN_TIME_IN_BAND_PCT:g}%)", **{k: temperature[k] for k in ("pct_in_band", "hours_below", "hours_above")})
    if humidity and humidity["night_hours_at_or_above_risk"] >= NIGHT_RISK_HOURS:
        add("amber", "night_humidity", f"humidity was at or above {HUMIDITY_RISK_PCT:g}% for "
            f"{humidity['night_hours_at_or_above_risk']:g} night hours (Botrytis risk)",
            hours=humidity["night_hours_at_or_above_risk"], max=humidity["max"])
    if forecast_out.get("coldest") and forecast_out["coldest"]["temperature_f"] <= FROST_F:
        add("amber", "forecast_frost", f"forecast low of {forecast_out['coldest']['temperature_f']:g}°F in the next "
            f"{FORECAST_HOURS} hours", **forecast_out["coldest"])
    if forecast_out.get("hottest") and forecast_out["hottest"]["temperature_f"] >= HEAT_F:
        add("amber", "forecast_heat", f"forecast high of {forecast_out['hottest']['temperature_f']:g}°F in the next "
            f"{FORECAST_HOURS} hours", **forecast_out["hottest"])
    if forecast_out.get("windiest") and forecast_out["windiest"]["wind_mph"] >= WIND_MPH:
        add("amber", "forecast_wind", f"forecast wind of {forecast_out['windiest']['wind_mph']:g} mph in the next "
            f"{FORECAST_HOURS} hours", **forecast_out["windiest"])

    status = max((f["severity"] for f in findings), key=watch.SEVERITY.get, default="green")
    gaps = [f"role not approved: {r}" for r in watch_result.get("roles_missing", [])]
    if not equipment:
        gaps.append("no equipment_* roles approved, so equipment runtime is not reported")
    return {"status": status, "nothing_to_report": not findings, "mode": mode, "cadence": cadence,
            "window_hours": hours, "findings": findings, "wake_daily_early": watch_result.get("status") != "green",
            "temperature": temperature, "outside_temperature": outside, "humidity": humidity,
            "equipment": equipment, "forecast": forecast_out, "data_gaps": gaps, "not_measured": NOT_MEASURED,
            "skipped": watch_result.get("skipped", []), "time_zone": time_zone or "UTC"}
