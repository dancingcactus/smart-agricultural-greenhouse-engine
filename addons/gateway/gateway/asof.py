"""As-of time handling: clip every query to a point in time so replays cannot see the future."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime

MAX_QUERY_LEN = 8000


class AsOfError(ValueError):
    """The request cannot be honoured under the as-of rules (maps to HTTP 400)."""


def parse_time(value: str | float) -> float:
    """Unix seconds or RFC3339 (naive timestamps are taken as UTC). Relative forms are refused."""
    if isinstance(value, (int, float)):
        result = float(value)
    else:
        text = str(value).strip()
        try:
            result = float(text)
        except ValueError:
            try:
                dt = datetime.fromisoformat(text)
            except ValueError as exc:
                raise AsOfError(f"unsupported time {value!r}: use unix seconds or RFC3339") from exc
            result = (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).timestamp()
    if not math.isfinite(result):
        raise AsOfError(f"unsupported time {value!r}")
    return result


def effective_as_of(header: str | None, forced: float | None) -> float | None:
    """The earliest of the caller's X-As-Of and the gateway-wide override; callers can only tighten."""
    values = []
    if header not in (None, ""):
        values.append(parse_time(header))
    if forced is not None:
        values.append(forced)
    return min(values) if values else None


def clip_end(requested: float | None, as_of: float | None, now: float) -> float:
    end = requested if requested is not None else (as_of if as_of is not None else now)
    return min(end, as_of) if as_of is not None else end


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


_DURATION = r"(?:\d+(?:\.\d+)?(?:ms|s|m|h|d|w|y|i))+"
_OFFSET = re.compile(r"\boffset\b", re.IGNORECASE)


def _strip_literals(query: str) -> str:
    """Blank out string literals and comments so '@' or 'offset -1h' inside them are not seen."""
    out: list[str] = []
    i, n = 0, len(query)
    while i < n:
        c = query[i]
        if c in "\"'`":
            i += 1
            while i < n and query[i] != c:
                i += 2 if query[i] == "\\" and c != "`" else 1
            i += 1
            out.append(c + c)
        elif c == "#":
            while i < n and query[i] != "\n":
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def check_promql(query: str) -> None:
    """Reject constructs that can evaluate data later than the query's end time.

    Conservative on purpose: `offset` is accepted only with a plain positive duration literal,
    so expressions that could evaluate to a negative offset are refused too, including any
    arithmetic right after the duration (`offset 1h-2h`), whose parsing differs between engines.
    """
    if len(query) > MAX_QUERY_LEN:
        raise AsOfError("query too long")
    text = _strip_literals(query)
    if "@" in text:
        raise AsOfError("the @ modifier is not allowed: it can read past the as-of time")
    if re.search(r"\[\s*-", text):
        raise AsOfError("negative lookback windows are not allowed")
    for match in _OFFSET.finditer(text):
        rest = text[match.end():]
        if rest.lstrip()[:1] in ("", "=", "!", "~", ",", "}", "{"):
            continue  # a label or metric that happens to be named "offset"
        if not re.match(rf"\s+{_DURATION}(?![\w.(])(?!\s*[-+*/%^])", rest):
            raise AsOfError("offset must be a plain positive duration such as 'offset 1h'")
