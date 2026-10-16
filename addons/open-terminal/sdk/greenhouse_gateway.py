"""Client for the Greenhouse gateway: the only way the agent's code reaches Home Assistant data.

Standard library only. It reads GATEWAY_URL and GATEWAY_KEY from the environment, can freeze every
query at a past time (as_of) for replays, and exposes read endpoints only: the gateway itself refuses
anything else. Meant to be imported by tools the agent writes:

    from greenhouse_gateway import Gateway
    gw = Gateway()                                   # live
    gw = Gateway(as_of="2026-10-01T12:00:00Z")       # replay: nothing after this time is visible
    gw.vm_query_range('avg_over_time(W_value{entity_id="plug_a_power"}[1h])', start=..., end=..., step="1h")
"""

# --- client (keep identical to owui/tools/greenhouse_gateway.py) ---
import json
import os
import urllib.error
import urllib.parse
import urllib.request


class GatewayError(RuntimeError):
    """The gateway refused or failed a request; the message says why (status code and detail)."""


class Gateway:
    def __init__(self, url=None, key=None, as_of=None, caller="agent", timeout=60, transport=None):
        self.url = (url or os.environ.get("GATEWAY_URL", "")).rstrip("/")
        self.key = key if key is not None else os.environ.get("GATEWAY_KEY", "")
        self.as_of, self.caller, self.timeout = as_of, caller, timeout
        self._transport = transport or self._urllib
        if not self.url:
            raise GatewayError("GATEWAY_URL is not set")

    def _headers(self):
        headers = {"X-Gateway-Key": self.key, "X-Caller": self.caller, "Accept": "application/json"}
        if self.as_of:
            headers["X-As-Of"] = str(self.as_of)
        return headers

    def _urllib(self, method, url, headers, body):
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:  # noqa: S310 - fixed http(s) gateway URL
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()
        except urllib.error.URLError as exc:
            raise GatewayError(f"cannot reach the gateway at {self.url}: {exc.reason}") from exc

    def _call(self, method, path, params=None, body=None):
        query = urllib.parse.urlencode([(k, v) for k, v in (params or {}).items() if v is not None], doseq=True)
        headers = self._headers()
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        status, raw = self._transport(method, f"{self.url}{path}{'?' + query if query else ''}", headers, data)
        text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        if status >= 400:
            try:
                detail = json.loads(text).get("detail", text)
            except ValueError:
                detail = text
            raise GatewayError(f"{method} {path} -> {status}: {detail}")
        return json.loads(text) if text.strip().startswith(("{", "[")) else text

    def get(self, path, /, **params):
        # `path` is positional-only so that a query parameter may itself be called "path".
        return self._call("GET", path, params)

    # catalog: which entities exist, and how they are stored in VictoriaMetrics
    def catalog_search(self, query, limit=25):
        return self.get("/catalog/metrics", q=query, limit=limit)

    def catalog_entity(self, entity_id):
        return self.get(f"/catalog/metrics/{entity_id}")

    def catalog_status(self):
        return self.get("/catalog/status")

    # Home Assistant (read-only)
    def states(self, domain=None, entity_id=None):
        return self.get("/ha/states", domain=domain, entity_id=entity_id)

    def history(self, entity_id, start=None, end=None, attributes=False):
        return self.get("/ha/history", entity_id=entity_id, start=start, end=end, attributes=str(attributes).lower())

    def logbook(self, start=None, end=None, entity_id=None):
        return self.get("/ha/logbook", start=start, end=end, entity_id=entity_id)

    def traces(self, automation_id=None, limit=20):
        return self.get("/ha/traces", automation_id=automation_id, limit=limit)

    # VictoriaMetrics (read-only, clipped to as_of)
    def vm_query(self, query, time=None):
        return self.get("/vm/query", query=query, time=time)

    def vm_query_range(self, query, start, end=None, step="60s"):
        return self.get("/vm/query_range", query=query, start=start, end=end, step=step)

    def vm_series(self, match, start=None, end=None):
        return self.get("/vm/series", **{"match[]": match, "start": start, "end": end})

    # weather, as forecast at the time
    def weather_forecast(self, type="hourly", entity_id=None, horizon_hours=None):
        return self.get("/weather/forecast", type=type, entity_id=entity_id, horizon_hours=horizon_hours)

    def weather_observations(self, start=None, end=None, entity_id=None):
        return self.get("/weather/observations", start=start, end=end, entity_id=entity_id)

    # evenly spaced history for forecasting (see greenhouse_forecast.py)
    def history_resampled(self, entity_id=None, role=None, step="1h", start=None, end=None, source=None):
        """Numeric history on a fixed grid: [[bucket_start_epoch, mean_or_None], ...] per entity. Give entity_id or role.

        source: "auto" (default: VictoriaMetrics, months of history, falling back to Home Assistant's roughly ten days),
        "vm" or "ha". Each series says which source it came from. Under an as-of time use entity_id, not role."""
        return self.get("/history/resampled", entity_id=entity_id, role=role, step=step, start=start, end=end,
                        source=source)

    def history_runtime(self, entity_id=None, role=None, days=14, end=None, timezone=None, source=None):
        """Hours switched on per local day (for example role equipment_gaht_fan), with run counts and completeness.
        source: "auto" (default), "vm" or "ha", as for history_resampled."""
        return self.get("/history/runtime", entity_id=entity_id, role=role, days=days, end=end, timezone=timezone,
                        source=source)

    # configuration snapshot and glossary
    def config_files(self):
        return self.get("/snapshot/files")

    def config_file(self, path):
        return self.get("/snapshot/file", path=path)

    def automations(self, managed=None, protected=None):
        return self.get("/snapshot/automations", managed=managed, protected=protected)

    def entity_usage(self, entity_id=None):
        return self.get("/snapshot/entity-usage", entity_id=entity_id)

    def glossary(self, q=None, status=None):
        return self.get("/glossary", q=q, status=status)

    def watch(self, stale_minutes=30, flat_hours=3, band_minutes=20):
        """Run the hourly code-only checks now: green/amber/red, findings with evidence, and what was skipped."""
        return self.get("/watch", stale_minutes=stale_minutes, flat_hours=flat_hours, band_minutes=band_minutes)

    def cadence(self):
        """The current mode and which loops (watch, daily, evening, weekly) it allows."""
        return self.get("/review/cadence")

    def daily_review(self, hours=24):
        """The last `hours` in numbers (time in band, night humidity, equipment runtime), the watch findings and
        the 72-hour forecast risks, as green/amber/red."""
        return self.get("/review/daily", hours=hours)

    def roles(self, include_drafts=False):
        """Role name -> entities (aliases written as role:<name> in the glossary). Approved only by default."""
        return self.get("/glossary/roles", include_drafts="true" if include_drafts else None)

    def propose_glossary(self, entity_id, meaning, aliases=None):
        """Suggest a meaning. It is only ever a draft: an owner decides whether to approve it."""
        return self._call("POST", "/glossary/drafts", body={"entity_id": entity_id, "meaning": meaning,
                                                             "aliases": aliases})
# --- end client ---
