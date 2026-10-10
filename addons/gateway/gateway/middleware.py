"""Auth, method policy and call logging for every request."""

from __future__ import annotations

import hmac
import re
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gateway import config
from gateway.calllog import CallLog
from gateway.redact import redact

# The gateway is read-only toward Home Assistant. These POSTs only refresh or add to its own data.
ALLOWED_POSTS = {"/catalog/run", "/snapshot/run", "/glossary/drafts"}
# Changes only an owner may make. "Owner" means the request arrived through Home Assistant's ingress
# proxy, which has already authenticated a Home Assistant admin; the API key is never enough.
OWNER_ONLY = (("PUT", re.compile(r"^/glossary/[a-z0-9_]+\.[a-z0-9_]+$")),)
OPEN_PATHS = {"/health"}


def is_owner(request: Request) -> bool:
    return bool(request.client) and request.client.host in config.owner_proxy_ips()


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def guard(request: Request, call_next):
        started = time.time()
        path, method = request.url.path, request.method
        owner = request.state.owner = is_owner(request)
        needs_owner = any(m == method and rx.match(path) for m, rx in OWNER_ONLY)
        refused = None
        if path not in OPEN_PATHS and not owner and config.api_key() and not hmac.compare_digest(
            request.headers.get("x-gateway-key", ""), config.api_key()
        ):
            refused = (401, "missing or wrong X-Gateway-Key")
        elif needs_owner and not owner:
            refused = (403, "owner only: make this change from the Greenhouse panel in Home Assistant")
        elif method not in ("GET", "HEAD") and not needs_owner and not (method == "POST" and path in ALLOWED_POSTS):
            refused = (405, "the gateway is read-only")
        if refused:
            response = JSONResponse({"detail": refused[1]}, status_code=refused[0])
        else:
            response = await call_next(request)

        log_path = config.call_log_path()
        if log_path:
            route = request.scope.get("route")
            CallLog(log_path).write({
                "ts": round(started, 3),
                "method": method,
                "path": path,
                "route": getattr(route, "path", None),
                "query": str(redact(request.url.query))[:2000],
                "as_of": request.headers.get("x-as-of"),
                "caller": request.headers.get("x-caller"),
                "owner": owner,
                "status": response.status_code,
                "refused": refused[1] if refused else None,
                "ms": round((time.time() - started) * 1000, 1),
            })
        return response
