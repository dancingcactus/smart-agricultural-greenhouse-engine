"""Read-only Home Assistant client.

This is the only module that holds a Home Assistant token. It exposes GET requests and a
websocket call restricted to registry *list* commands; no service-call method exists.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from websockets.sync.client import connect

ALLOWED_WS_COMMANDS = frozenset(
    {
        "config/entity_registry/list",
        "config/device_registry/list",
        "config/area_registry/list",
        "config/label_registry/list",
    }
)


class HAClient:
    def __init__(self, base_url: str, token: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._http = httpx.Client(
            base_url=self.base_url + "/api",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    @classmethod
    def from_env(cls) -> HAClient:
        token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HA_TOKEN")
        if not token:
            raise RuntimeError("Set SUPERVISOR_TOKEN (add-on) or HA_TOKEN (standalone)")
        default = "http://supervisor/core" if os.environ.get("SUPERVISOR_TOKEN") else None
        base = os.environ.get("HA_URL", default)
        if not base:
            raise RuntimeError("Set HA_URL")
        return cls(base, token)

    def get(self, path: str, **params: Any) -> Any:
        resp = self._http.get(path, params=params or None)
        resp.raise_for_status()
        return resp.json()

    def states(self) -> list[dict]:
        return self.get("/states")

    def ws_list(self, command: str) -> list[dict]:
        if command not in ALLOWED_WS_COMMANDS:
            raise PermissionError(f"websocket command not allowed: {command}")
        scheme_swapped = self.base_url.replace("http", "ws", 1)
        # Supervisor proxy serves /core/websocket; a direct HA URL serves /api/websocket.
        ws_url = scheme_swapped + ("/websocket" if self.base_url.endswith("/core") else "/api/websocket")
        with connect(ws_url) as ws:
            json.loads(ws.recv())  # auth_required
            ws.send(json.dumps({"type": "auth", "access_token": self._token}))
            if json.loads(ws.recv()).get("type") != "auth_ok":
                raise PermissionError("Home Assistant websocket auth failed")
            ws.send(json.dumps({"id": 1, "type": command}))
            reply = json.loads(ws.recv())
        if not reply.get("success"):
            raise RuntimeError(f"{command} failed: {reply.get('error')}")
        return reply["result"]
