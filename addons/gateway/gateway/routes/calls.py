from __future__ import annotations

from collections import Counter

from fastapi import APIRouter

from gateway import config
from gateway.calllog import CallLog

router = APIRouter(prefix="/calls", tags=["call log"])


@router.get("/summary")
def summary(since: float = 0.0):
    """Which resources are actually being used, and which calls were refused."""
    path = config.call_log_path()
    rows = [r for r in (CallLog(path).read() if path else []) if r["ts"] >= since]
    return {
        "total": len(rows),
        "by_route": Counter(f'{r["method"]} {r["route"] or r["path"]}' for r in rows),
        "by_status": Counter(str(r["status"]) for r in rows),
        "by_caller": Counter(r["caller"] or "-" for r in rows),
        "refused": [r for r in rows if r["refused"]][-50:],
    }
