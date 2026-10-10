"""
title: Greenhouse Gateway
author: Greenhouse AI Operator
version: 0.1.0
description: Read-only access to the greenhouse's data (entities, history, weather forecasts, configuration, glossary) through the Greenhouse Gateway. It cannot change anything in Home Assistant.
"""

# --- client (generated from addons/open-terminal/sdk/greenhouse_gateway.py; do not edit here) ---
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

    def propose_glossary(self, entity_id, meaning, aliases=None):
        """Suggest a meaning. It is only ever a draft: an owner decides whether to approve it."""
        return self._call("POST", "/glossary/drafts", body={"entity_id": entity_id, "meaning": meaning,
                                                             "aliases": aliases})
# --- end client ---

from pydantic import BaseModel, Field  # noqa: E402 - provided by Open WebUI

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _iso(ts):
    import datetime

    return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime(_ISO)


def summarize_series(item, max_points=24):
    """One VictoriaMetrics result row reduced to its shape: size, range, ends and a few samples."""
    values = item.get("values") or ([item["value"]] if "value" in item else [])
    nums = []
    for ts, raw in values:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value == value and abs(value) != float("inf"):
            nums.append((float(ts), value))
    metric = {k: v for k, v in item.get("metric", {}).items() if k != "db"}
    if not nums:
        return {"metric": metric, "points": 0}
    ys = [v for _, v in nums]
    stride = max(1, -(-len(nums) // max(1, max_points)))
    return {
        "metric": metric, "points": len(nums), "min": min(ys), "max": max(ys),
        "mean": round(sum(ys) / len(ys), 4),
        "first": [_iso(nums[0][0]), nums[0][1]], "last": [_iso(nums[-1][0]), nums[-1][1]],
        "sample": [[_iso(t), v] for t, v in nums[::stride]],
    }


def summarize_history(rows, max_changes=40):
    out = {}
    for series in rows:
        if not series:
            continue
        changes = [(s.get("last_changed") or s.get("last_updated"), s.get("state")) for s in series]
        out[series[0].get("entity_id", "?")] = {"changes": len(changes), "first": changes[0], "last": changes[-1],
                                                "sample": changes[:: max(1, -(-len(changes) // max_changes))]}
    return out


class Tools:
    class Valves(BaseModel):
        GATEWAY_URL: str = Field(
            default="", description="Gateway address on the Home Assistant network, e.g. http://abc123-greenhouse-gateway:8099")
        GATEWAY_KEY: str = Field(default="", description="The gateway's api_key.")
        AS_OF: str = Field(
            default="", description="Leave empty for live data. Set a time (2026-10-01T12:00:00Z) to replay: every answer "
                                    "then reflects only what was known by that time.")
        MAX_CHARS: int = Field(default=6000, description="Longest answer returned to the model, in characters.")

    def __init__(self):
        self.valves = self.Valves()
        self.citation = False

    # -- plumbing -------------------------------------------------------------------------------
    def _gw(self):
        return Gateway(self.valves.GATEWAY_URL, self.valves.GATEWAY_KEY, as_of=self.valves.AS_OF or None,
                       caller="open-webui")

    def _out(self, data):
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
        limit = max(500, self.valves.MAX_CHARS)
        if len(text) > limit:
            text = text[:limit] + f"... [truncated at {limit} characters; ask for a narrower question]"
        return text

    def _run(self, fn):
        try:
            return self._out(fn(self._gw()))
        except GatewayError as exc:
            return f"Gateway error: {exc}"

    # -- tools the model can call -----------------------------------------------------------------
    def find_entities(self, query: str, limit: int = 10) -> str:
        """
        Search the greenhouse's entities by name, id, area, unit, label or metric name.
        Use this first to learn the exact entity_id and how its history is stored.
        :param query: Words to look for, e.g. "humidity" or "gaht exit".
        :param limit: Most results to return.
        :return: Matching entities with unit, area, status and the VictoriaMetrics series selector for each.
        """
        def go(gw):
            rows = gw.catalog_search(query, limit=limit)
            return [{"entity_id": r["entity_id"], "name": r["friendly_name"] or r["name"], "unit": r["unit"],
                     "area": r["area"], "device": r.get("device_name"), "status": r["vm_status"],
                     "series": [{"metric": s["metric"], "selector": s["selector"], "samples": s["samples"],
                                 "history_from": s.get("history_first_seen")} for s in r["series"] if s["kind"] == "value"]}
                    for r in rows]
        return self._run(go)

    def query_metric(self, promql: str, time: str = "") -> str:
        """
        Run an instant VictoriaMetrics (PromQL) query: the value at one moment.
        Use the selectors from find_entities. Only past data is available; "@" and negative offsets are refused.
        :param promql: The query, e.g. avg_over_time({__name__="W_value",entity_id="plug_a_power"}[1h]).
        :param time: Optional moment as RFC3339 or unix seconds; defaults to now (or the replay time).
        :return: Each resulting series reduced to its value.
        """
        return self._run(lambda gw: [summarize_series(r) for r in gw.vm_query(promql, time or None)["data"]["result"]])

    def query_metric_range(self, promql: str, start: str, end: str = "", step: str = "1h") -> str:
        """
        Run a VictoriaMetrics (PromQL) query over a time range and get a summary of each series.
        :param promql: The query, using selectors from find_entities.
        :param start: Start as RFC3339 or unix seconds.
        :param end: End; defaults to now (or the replay time). It is never later than the replay time.
        :param step: Resolution, e.g. 5m, 1h, 1d.
        :return: For each series: number of points, min, max, mean, first, last and a few sample points.
        """
        return self._run(lambda gw: [summarize_series(r) for r in
                                     gw.vm_query_range(promql, start, end or None, step)["data"]["result"]])

    def entity_history(self, entity_id: str, start: str = "", end: str = "") -> str:
        """
        State changes of one Home Assistant entity over a period (defaults to the last 24 hours).
        :param entity_id: For example input_boolean.heater.
        :param start: Start as RFC3339 or unix seconds.
        :param end: End as RFC3339 or unix seconds.
        :return: How many changes, the first and last, and a sample.
        """
        return self._run(lambda gw: summarize_history(gw.history(entity_id, start or None, end or None)))

    def weather_forecast(self, type: str = "hourly", horizon_hours: int = 24) -> str:
        """
        The weather forecast as it was known at the time (the replay time, or now): archived, not recomputed.
        :param type: hourly, daily or twice_daily.
        :param horizon_hours: How many hours ahead of the forecast's issue time to return.
        :return: When the forecast was issued, its units, and the forecast points.
        """
        return self._run(lambda gw: gw.weather_forecast(type, horizon_hours=horizon_hours))

    def glossary(self, query: str = "") -> str:
        """
        The owners' plain-English meanings of entities, e.g. which meter is the GAHT exit sensor.
        Approved entries are trustworthy; drafts are only suggestions.
        :param query: Words to filter by; empty lists the entries that most need explaining.
        :return: Entries with meaning, status, device and where each is used.
        """
        def go(gw):
            rows = gw.glossary(q=query or None)
            return [{k: r.get(k) for k in ("entity_id", "friendly_name", "meaning", "status", "aliases", "device_name",
                                           "usage")} for r in rows[:15]]
        return self._run(go)

    def suggest_meaning(self, entity_id: str, meaning: str) -> str:
        """
        Suggest a plain-English meaning for an entity. This only saves a draft: an owner reviews and approves it.
        :param entity_id: The entity to describe.
        :param meaning: One or two sentences, based on evidence rather than guesses.
        :return: The saved draft.
        """
        return self._run(lambda gw: gw.propose_glossary(entity_id, meaning))

    def automations(self, query: str = "") -> str:
        """
        The Home Assistant automations: purpose, triggers, services they call, entities they touch, last run,
        and whether each is protected or agent-managed.
        :param query: Part of a name or entity id to filter by; empty lists them all.
        :return: Matching automations.
        """
        def go(gw):
            needle = query.lower()
            rows = [a for a in gw.automations() if needle in f"{a['alias']} {a['entity_id']}".lower()]
            return [{k: a.get(k) for k in ("alias", "entity_id", "description", "enabled", "last_triggered", "protected",
                                           "managed", "triggers", "services", "entities")} for a in rows[:12]]
        return self._run(go)

    def where_is_entity_used(self, entity_id: str) -> str:
        """
        Which automations, scripts, scenes and dashboards use an entity.
        :param entity_id: For example input_number.hot_alarm_temp.
        :return: The places it is used, with their names.
        """
        return self._run(lambda gw: gw.entity_usage(entity_id))

    def read_config_file(self, path: str) -> str:
        """
        Read one file of the mirrored Home Assistant configuration (secrets are blanked), as of the replay time.
        :param path: For example automations.yaml or packages/heating.yaml.
        :return: The file's text.
        """
        return self._run(lambda gw: gw.config_file(path))
