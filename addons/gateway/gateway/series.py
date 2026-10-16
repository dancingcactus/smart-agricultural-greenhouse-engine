"""Clean, evenly spaced series for forecasting: the data feed the agent's forecasting library builds on.

Like `watch` and `review`, everything here is pure: the functions take already-fetched history, and the route in
`routes/series.py` does the fetching and the as-of clipping. Nothing here makes a prediction.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

from gateway import review, watch

STEP_RE = re.compile(r"^(\d+)(m|h)$")
MIN_STEP_S, MAX_STEP_S = 300, 86400
MAX_POINTS = 20000


class SeriesError(ValueError):
    """The request cannot be served as asked (maps to HTTP 400)."""


def step_seconds(step: str) -> int:
    """'15m' or '1h' as seconds. Steps run from 5 minutes to a day."""
    match = STEP_RE.match(step.strip().lower())
    if not match:
        raise SeriesError("step must look like 15m or 1h")
    seconds = int(match.group(1)) * (60 if match.group(2) == "m" else 3600)
    if not MIN_STEP_S <= seconds <= MAX_STEP_S:
        raise SeriesError("step must be between 5m and 24h")
    return seconds


def resample(points: list, start: float, end: float, step: int) -> list[list]:
    """Time-weighted mean per bucket as [bucket_start, value]; None where the bucket has no numeric reading.

    History is a step function: a reading holds until the next one. A reading that is unavailable or not a
    number ends the previous reading, so gaps stay gaps instead of being filled. Only whole buckets inside
    [start, end] are returned, so the last value is never a partial average.
    """
    if end <= start:
        return []
    first = math.ceil(start / step) * step
    n = int((end - first) // step)
    if n > MAX_POINTS:
        raise SeriesError(f"too many points ({n}); use a longer step or a shorter window")
    readings = sorted(points)
    out: list[list] = []
    for i in range(n):
        a, b = first + i * step, first + (i + 1) * step
        total = weight = 0.0
        for s, e, v in review._segments(readings, a, b):
            try:
                x = float(v)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(x):
                continue
            total += x * (e - s)
            weight += e - s
        out.append([a, round(total / weight, 4) if weight else None])
    return out


def daily_runtime(points: list, start: float, end: float, tz) -> list[dict]:
    """Hours switched on per local day for a piece of equipment, from its state history.

    A day is `complete` when the window covers all of it and the state was known for all but a few minutes.
    """
    pts = sorted(points)
    day = datetime.fromtimestamp(start, tz).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    while day.timestamp() < end:
        nxt = (day + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        a, b = max(day.timestamp(), start), min(nxt.timestamp(), end)
        summary = review.equipment_summary(pts, b, (b - a) / 3600)
        known = sum(e - s for s, e, v in review._segments(pts, a, b) if str(v).lower() not in watch.BAD_STATES)
        out.append({"date": day.date().isoformat(), "hours_on": summary["runtime_hours"], "runs": summary["runs"],
                    "known_hours": round(known / 3600, 2),
                    "complete": a == day.timestamp() and b == nxt.timestamp() and (b - a) - known <= 900})
        day = nxt
    return out
