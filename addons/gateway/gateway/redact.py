"""Strip token-like values from anything returned to the agent side."""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEY = re.compile(r"token|password|passwd|secret|api_?key|authorization|webhook", re.IGNORECASE)
TOKEN_PARAM = re.compile(r"([?&](?:token|access_token|authsig|api_?key)=)[^&\s\"']+", re.IGNORECASE)
REDACTED = "[redacted]"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: (REDACTED if SENSITIVE_KEY.search(str(k)) else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return TOKEN_PARAM.sub(rf"\1{REDACTED}", value)
    return value
