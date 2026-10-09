"""Read-only Home Assistant client.

This is the only module that holds a Home Assistant token. It exposes GET requests and a
websocket call restricted to registry *list* commands; no service-call method exists.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any

import httpx
from websockets.sync.client import connect

ALLOWED_WS_COMMANDS = frozenset(
    {
        "config/entity_registry/list",
        "config/device_registry/list",
        "config/area_registry/list",
        "config/label_registry/list",
        "trace/list",
        "trace/get",
    }
)
TRACE_DOMAINS = frozenset({"automation"})
# Read-only subscriptions: the frontend's way of getting forecasts without calling a service.
ALLOWED_SUBSCRIPTIONS = frozenset({"weather/subscribe_forecast"})
FORECAST_TYPES = frozenset({"hourly", "daily", "twice_daily"})


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
        return self.ws_call(command)

    @contextmanager
    def _ws(self):
        """An authenticated websocket to Home Assistant."""
        scheme_swapped = self.base_url.replace("http", "ws", 1)
        # Supervisor proxy serves /core/websocket; a direct HA URL serves /api/websocket.
        ws_url = scheme_swapped + ("/websocket" if self.base_url.endswith("/core") else "/api/websocket")
        with connect(ws_url) as ws:
            json.loads(ws.recv())  # auth_required
            ws.send(json.dumps({"type": "auth", "access_token": self._token}))
            if json.loads(ws.recv()).get("type") != "auth_ok":
                raise PermissionError("Home Assistant websocket auth failed")
            yield ws

    def ws_call(self, command: str, **params: Any) -> Any:
        """Run one allowlisted read-only websocket command (registry lists, automation traces)."""
        if command not in ALLOWED_WS_COMMANDS:
            raise PermissionError(f"websocket command not allowed: {command}")
        if command.startswith("trace/") and params.get("domain") not in TRACE_DOMAINS:
            raise PermissionError("traces are only available for automations")
        with self._ws() as ws:
            ws.send(json.dumps({"id": 1, "type": command, **params}))
            reply = json.loads(ws.recv())
        if not reply.get("success"):
            raise RuntimeError(f"{command} failed: {reply.get('error')}")
        return reply["result"]

    def ws_first_event(self, command: str, timeout: float = 20.0, **params: Any) -> Any:
        """Subscribe, return the first event, and close (which ends the subscription)."""
        if command not in ALLOWED_SUBSCRIPTIONS:
            raise PermissionError(f"websocket subscription not allowed: {command}")
        if not str(params.get("entity_id", "")).startswith("weather.") \
                or params.get("forecast_type") not in FORECAST_TYPES:
            raise PermissionError("forecast subscriptions need a weather.* entity and a known forecast_type")
        with self._ws() as ws:
            ws.send(json.dumps({"id": 1, "type": command, **params}))
            while True:
                msg = json.loads(ws.recv(timeout=timeout))
                if msg.get("id") != 1:
                    continue
                if msg.get("type") == "result" and not msg.get("success"):
                    raise RuntimeError(f"{command} failed: {msg.get('error')}")
                if msg.get("type") == "event":
                    return msg["event"]
