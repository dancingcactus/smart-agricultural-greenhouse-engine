"""
title: SAGE Gateway
author: SAGE
version: 0.3.0
description: Read-only access to the greenhouse's data (entities, history, weather forecasts, configuration, glossary) through the SAGE Gateway. It cannot change anything in Home Assistant.
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

    def watch(self, stale_minutes=30, flat_hours=3, band_minutes=20):
        """Run the hourly code-only checks now: green/amber/red, findings with evidence, and what was skipped."""
        return self.get("/watch", stale_minutes=stale_minutes, flat_hours=flat_hours, band_minutes=band_minutes)

    def roles(self, include_drafts=False):
        """Role name -> entities (aliases written as role:<name> in the glossary). Approved only by default."""
        return self.get("/glossary/roles", include_drafts="true" if include_drafts else None)

    def propose_glossary(self, entity_id, meaning, aliases=None):
        """Suggest a meaning. It is only ever a draft: an owner decides whether to approve it."""
        return self._call("POST", "/glossary/drafts", body={"entity_id": entity_id, "meaning": meaning,
                                                             "aliases": aliases})
# --- end client ---

from fastapi.responses import HTMLResponse  # noqa: E402 - provided by Open WebUI
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


# ---- charts: drawn for the grower, inline in the chat. The model only passes role names; the numbers come from the
# gateway and the picture is built here, so the model never types data and cannot mis-quote it. -----------------------
_SLOTS = (("greenhouse", 1), ("outside", 2), ("control_room", 3), ("gaht_input", 4), ("gaht_exit", 5))
MAX_CHART_SERIES = 5
_DEFAULT_UNITS = (("_temperature", "°F"), ("_humidity", "%"), ("vpd", "kPa"))


def _epoch(value):
    import datetime

    try:
        return datetime.datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return None


def history_points(rows, end_ts):
    """Home Assistant history for one entity -> [[epoch_ms, number]], oldest first. Non-numeric states are dropped.
    The last value is repeated at `end_ts` so a sensor that has not changed still reaches the right edge."""
    points = []
    for series in rows or []:
        for item in series:
            ts = _epoch(item.get("last_changed") or item.get("last_updated"))
            try:
                value = float(item.get("state"))
            except (TypeError, ValueError):
                continue
            if ts is not None and value == value and abs(value) != float("inf"):
                points.append([int(ts * 1000), value])
        break  # one entity per request
    if points and points[-1][0] < end_ts * 1000:
        points.append([int(end_ts * 1000), points[-1][1]])
    return points


def downsample(points, max_points=400):
    """Average into at most `max_points` time buckets so a week of readings stays light."""
    if len(points) <= max_points:
        return points
    lo, hi = points[0][0], points[-1][0]
    width = max(1, (hi - lo) / max_points)
    buckets = {}
    for t, v in points:
        buckets.setdefault(int((t - lo) // width), []).append((t, v))
    return [[round(sum(t for t, _ in b) / len(b)), round(sum(v for _, v in b) / len(b), 3)] for _, b in sorted(buckets.items())]


def value_at(points, ts_ms):
    """The last known value at or before ts_ms (a step lookup), or None."""
    found = None
    for t, v in points:
        if t > ts_ms:
            break
        found = v
    return found


def band_stats(points, low, high):
    """Share of the time the readings were below, inside and above the band (time-weighted). A reading holds until
    the next one, and the band is read as it was at each moment, so a setpoint that moves mid-way is handled."""
    spans = {"below": 0.0, "inside": 0.0, "above": 0.0}
    changes = sorted({t for t, _ in low} | {t for t, _ in high})
    for (t0, v), (t1, _) in zip(points, points[1:]):
        cuts = [t0] + [t for t in changes if t0 < t < t1] + [t1]
        for a, b in zip(cuts, cuts[1:]):
            lo, hi = value_at(low, a), value_at(high, a)
            if lo is not None and hi is not None:
                spans["below" if v < lo else "above" if v > hi else "inside"] += b - a
    total = sum(spans.values())
    return None if not total else {k: round(100 * s / total, 1) for k, s in spans.items()}


def series_stats(points):
    values = [v for _, v in points]
    return {"latest": values[-1], "min": min(values), "max": max(values), "mean": round(sum(values) / len(values), 2),
            "points": len(values)}


def assign_slots(roles):
    """A fixed colour per kind of sensor, so 'greenhouse' is always the same colour from chart to chart."""
    slots, used = {}, set()
    for role in roles:
        slot = next((s for prefix, s in _SLOTS if role.startswith(prefix) and s not in used), None)
        if slot:
            slots[role] = slot
            used.add(slot)
    free = iter(s for s in range(1, 9) if s not in used)
    for role in roles:
        if role not in slots:
            slots[role] = next(free)
    return slots


def build_chart_spec(title, start_ts, end_ts, series, bands):
    """series: [{role, entity_id, unit, points}]; bands: {unit: {"low": pts, "high": pts}}. One panel per unit,
    never a second axis on the same panel."""
    slots = assign_slots([s["role"] for s in series])
    panels = {}
    for s in series:
        panel = panels.setdefault(s["unit"], {"unit": s["unit"], "series": [], "band": None})
        panel["series"].append({"role": s["role"], "label": s["role"].replace("_", " ").capitalize(),
                                "entity_id": s["entity_id"], "slot": slots[s["role"]], "stats": series_stats(s["points"]),
                                "points": downsample(s["points"])})
        band = bands.get(s["unit"])
        if band and s["role"] == "greenhouse_temperature":
            panel["band"] = {"label": "Target band", "low": band["low"], "high": band["high"],
                             "stats": band_stats(s["points"], band["low"], band["high"])}
    return {"title": title, "start": int(start_ts * 1000), "end": int(end_ts * 1000), "panels": list(panels.values())}


_CHART_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{color-scheme:light dark;--bg:#fcfcfb;--fg:#0b0b0b;--muted:#52514e;--grid:#e3e2de;--band:rgba(80,120,160,.14);
--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;--s4:#eda100;--s5:#e87ba4;--s6:#008300;--s7:#6250d6;--s8:#e34948}
@media (prefers-color-scheme:dark){:root{--bg:#1a1a19;--fg:#f0efec;--muted:#c3c2b7;--grid:#34342f;--band:rgba(120,160,200,.16);
--s1:#3987e5;--s2:#d95926;--s3:#199e70;--s4:#c98500;--s5:#d55181;--s6:#008300;--s7:#9085e9;--s8:#e66767}}
*{box-sizing:border-box}body{margin:0;font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--fg)}
#root{background:var(--bg);border:1px solid var(--grid);border-radius:12px;padding:12px 14px}
h1{font-size:1rem;margin:0 0 2px}.sub{color:var(--muted);font-size:.8rem}.legend{display:flex;gap:14px;flex-wrap:wrap;margin:8px 0;font-size:.85rem}
.legend span{display:inline-flex;align-items:center;gap:6px}.legend i{width:10px;height:10px;border-radius:2px;display:inline-block}
.panel{position:relative;margin-top:8px;outline-offset:2px}.panel .unit{color:var(--muted);font-size:.75rem}
svg{display:block;width:100%;overflow:visible}.axis{fill:var(--muted);font-size:11px}
.tip{position:absolute;z-index:5;pointer-events:none;background:var(--bg);border:1px solid var(--grid);border-radius:6px;padding:6px 9px;
font-size:.8rem;box-shadow:0 2px 8px rgba(0,0,0,.2);min-width:150px}.tip .t{color:var(--muted);font-size:.72rem;margin-bottom:2px}
.tip .r{display:flex;align-items:center;gap:6px}.tip .r i{width:14px;border-top:3px solid;display:inline-block}.tip .r b{margin-left:auto;padding-left:10px}
details{margin-top:8px;font-size:.82rem;color:var(--muted)}table{border-collapse:collapse;margin-top:4px;width:100%}
td,th{text-align:right;padding:2px 8px;border-bottom:1px solid var(--grid);white-space:nowrap}td:first-child,th:first-child{text-align:left}
</style></head><body><div id="root"></div>
<script id="spec" type="application/json">__SPEC__</script>
<script>
(() => {
const spec = JSON.parse(document.getElementById('spec').textContent);
const NS = 'http://www.w3.org/2000/svg', root = document.getElementById('root');
const el = (t, c, x) => { const n = document.createElement(t); if (c) n.className = c; if (x !== undefined) n.textContent = x; return n; };
const sv = (t, a) => { const n = document.createElementNS(NS, t); for (const k in a) n.setAttribute(k, a[k]); return n; };
const num = (v) => Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1), tick = (v) => Number.isInteger(v) ? String(v) : v.toFixed(1);
const long = spec.end - spec.start > 36 * 3600e3;
const tf = (ms, full) => new Date(ms).toLocaleString([], (full || long) ? { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' } : { hour: 'numeric', minute: '2-digit' });
const at = (pts, t) => { let r = null; for (const p of pts) { if (p[0] > t) break; r = p[1]; } return r; };
function ticks(lo, hi) {
  const span = hi - lo || 1, raw = span / 4, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10, out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}
function stepPath(pts, x, y) { let d = ''; pts.forEach((p, i) => { d += (i ? 'H' + x(p[0]) + 'V' + y(p[1]) : 'M' + x(p[0]) + ',' + y(p[1])); }); return d; }
function panel(p) {
  const wrap = el('div', 'panel'); wrap.tabIndex = 0;
  wrap.append(el('div', 'unit', p.unit));
  const W = Math.max(280, root.clientWidth - 28), H = 190, L = 44, R = 10, T = 8, B = 22;
  const vals = p.series.flatMap((s) => s.points.map((q) => q[1])).concat(p.band ? p.band.low.concat(p.band.high).map((q) => q[1]) : []);
  let lo = Math.min(...vals), hi = Math.max(...vals); const pad = (hi - lo || 1) * 0.06; lo -= pad; hi += pad;
  const x = (t) => L + (t - spec.start) / (spec.end - spec.start || 1) * (W - L - R), y = (v) => T + (hi - v) / (hi - lo) * (H - T - B);
  const svg = sv('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': p.unit + ' chart' }); svg.style.height = H + 'px';
  for (const v of ticks(lo, hi)) {
    svg.append(sv('line', { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: 'var(--grid)', 'stroke-width': 1 }));
    const t = sv('text', { x: L - 6, y: y(v) + 4, 'text-anchor': 'end', class: 'axis' }); t.textContent = tick(v); svg.append(t);
  }
  for (let i = 0; i <= 4; i++) {
    const t = spec.start + (spec.end - spec.start) * i / 4, a = sv('text', { x: Math.min(W - R, Math.max(L, x(t))), y: H - 4, class: 'axis', 'text-anchor': i === 0 ? 'start' : i === 4 ? 'end' : 'middle' });
    a.textContent = tf(t); svg.append(a);
  }
  if (p.band) {
    const hiP = p.band.high, loP = p.band.low, end = spec.end;
    const top = hiP.map((q) => [q[0], q[1]]), bot = loP.map((q) => [q[0], q[1]]);
    let d = 'M' + x(top[0][0]) + ',' + y(top[0][1]);
    top.forEach((q, i) => { if (i) d += 'H' + x(q[0]) + 'V' + y(q[1]); }); d += 'H' + x(end);
    d += 'V' + y(bot[bot.length - 1][1]); for (let i = bot.length - 1; i > 0; i--) d += 'H' + x(bot[i][0]) + 'V' + y(bot[i - 1][1]);
    d += 'H' + x(bot[0][0]) + 'Z';
    svg.append(sv('path', { d, fill: 'var(--band)' }));
  }
  for (const s of p.series) {
    svg.append(sv('path', { d: s.points.map((q, i) => (i ? 'L' : 'M') + x(q[0]).toFixed(1) + ',' + y(q[1]).toFixed(1)).join(''), fill: 'none',
      stroke: 'var(--s' + s.slot + ')', 'stroke-width': 2, 'stroke-linejoin': 'round', 'stroke-linecap': 'round' }));
    const last = s.points[s.points.length - 1];
    svg.append(sv('circle', { cx: x(last[0]), cy: y(last[1]), r: 4, fill: 'var(--s' + s.slot + ')', stroke: 'var(--bg)', 'stroke-width': 2 }));
  }
  const cross = sv('line', { y1: T, y2: H - B, stroke: 'var(--muted)', 'stroke-width': 1, visibility: 'hidden' }); svg.append(cross);
  wrap.append(svg);
  const tip = el('div', 'tip'); tip.hidden = true; wrap.append(tip);
  const show = (t) => {
    t = Math.min(spec.end, Math.max(spec.start, t)); cross.setAttribute('x1', x(t)); cross.setAttribute('x2', x(t)); cross.setAttribute('visibility', 'visible');
    tip.replaceChildren(el('div', 't', tf(t, true)));
    for (const s of p.series) {
      const v = at(s.points, t); if (v === null) continue;
      const r = el('div', 'r'), k = el('i'); k.style.borderTopColor = 'var(--s' + s.slot + ')';
      r.append(k, el('span', '', s.label), el('b', '', num(v) + ' ' + p.unit)); tip.append(r);
    }
    if (p.band) { const a = at(p.band.low, t), b = at(p.band.high, t); if (a !== null && b !== null) { const r = el('div', 'r'); r.append(el('span', '', p.band.label), el('b', '', num(a) + '-' + num(b))); tip.append(r); } }
    tip.hidden = false; const px = x(t) / W * wrap.clientWidth; tip.style.top = '18px';
    tip.style.left = Math.max(0, Math.min(wrap.clientWidth - tip.offsetWidth - 4, px + 10)) + 'px';
  };
  const hide = () => { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); };
  svg.onpointermove = (e) => { const r = svg.getBoundingClientRect(); show(spec.start + ((e.clientX - r.left) / r.width * W - L) / (W - L - R) * (spec.end - spec.start)); };
  svg.onpointerleave = hide; wrap.onblur = hide;
  let cur = spec.end; wrap.onkeydown = (e) => { const d = (spec.end - spec.start) / 60; if (e.key === 'ArrowLeft') { cur -= d; show(cur); e.preventDefault(); } if (e.key === 'ArrowRight') { cur += d; show(cur); e.preventDefault(); } };
  return wrap;
}
function render() {
  width = root.clientWidth;
  root.replaceChildren(el('h1', '', spec.title), el('div', 'sub', tf(spec.start, true) + ' to ' + tf(spec.end, true)));
  const all = spec.panels.flatMap((p) => p.series);
  if (all.length > 1) {
    const lg = el('div', 'legend');
    for (const s of all) { const b = el('span'), i = el('i'); i.style.background = 'var(--s' + s.slot + ')'; b.append(i, document.createTextNode(s.label)); lg.append(b); }
    root.append(lg);
  }
  for (const p of spec.panels) root.append(panel(p));
  const d = el('details'), tb = el('table'); d.append(el('summary', '', 'Numbers')); d.append(tb);
  const head = el('tr'); ['', 'Latest', 'Low', 'High', 'Average'].forEach((h) => head.append(el('th', '', h))); tb.append(head);
  for (const p of spec.panels) {
    for (const s of p.series) { const r = el('tr'); r.append(el('td', '', s.label + ' (' + p.unit + ')')); for (const k of ['latest', 'min', 'max', 'mean']) r.append(el('td', '', num(s.stats[k]))); tb.append(r); }
    if (p.band && p.band.stats) { const r = el('tr'); r.append(el('td', '', p.band.label + ': below / inside / above (% of time)')); const b = p.band.stats; r.append(el('td', '', b.below + ' / ' + b.inside + ' / ' + b.above)); tb.append(r); }
  }
  root.append(d); post();
}
function post() { try { parent.postMessage({ type: 'iframe:height', height: document.documentElement.scrollHeight + 4 }, '*'); } catch (e) { /* standalone */ } }
let timer, width = 0; const again = () => { clearTimeout(timer); timer = setTimeout(() => { if (root.clientWidth !== width) render(); }, 120); };
render(); window.addEventListener('resize', again); if (window.ResizeObserver) new ResizeObserver(post).observe(document.body);
})();
</script></body></html>"""


def chart_html(spec):
    """The page for one chart. The spec is data inside a JSON script tag, never markup (`<` is escaped)."""
    return _CHART_HTML.replace("__SPEC__", json.dumps(spec, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c"))


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

    def run_watch(self) -> str:
        """
        Run the greenhouse health checks right now (plain code, no guesswork): stale or stuck sensors, low
        batteries, the greenhouse temperature outside the live setpoints or alarm limits, thermostat faults.
        :return: status green/amber/red, findings with evidence, current readings, roles still missing, and
                 what was not checked. Report only what it found; "nothing to report" is a normal answer.
        """
        return self._run(lambda gw: gw.watch())

    def show_chart(self, roles: str, hours: float = 24, title: str = "") -> str:
        """
        Draw a chart of recent readings for the grower, shown inline in the chat. Use it when a picture would help
        the grower see what happened. You do not need to describe the picture: it is displayed, and the numbers you
        may quote (latest, low, high, average, share of time inside the target band) are returned here.
        :param roles: Role names from get_roles, comma separated, at most 5 (for example "greenhouse_temperature,outside_temperature"). Readings with different units are drawn in separate panels. The target band is added for greenhouse_temperature.
        :param hours: How far back to look, 1 to 168.
        :param title: A short title, for example "Last 24 hours: greenhouse and outside temperature".
        :return: The chart, plus the numbers behind it.
        """
        import time as _time

        wanted = [r.strip().lower() for r in (roles or "").split(",") if r.strip()]
        if not wanted or len(wanted) > MAX_CHART_SERIES:
            return f"Give 1 to {MAX_CHART_SERIES} role names, comma separated."
        hours = min(168.0, max(1.0, float(hours or 24)))
        try:
            gw = self._gw()
            known = gw.roles()
            missing = [r for r in wanted if r not in known]
            if missing:
                return f"Unknown role(s): {', '.join(missing)}. Available roles: {', '.join(sorted(known)) or 'none approved yet'}."
            end_ts = (_epoch(self.valves.AS_OF) if self.valves.AS_OF else None) or _time.time()
            start_ts = end_ts - hours * 3600

            def pull(entity_id):
                rows = gw.history(entity_id, _iso(start_ts), _iso(end_ts))
                return history_points(rows, end_ts)

            states = {x["entity_id"]: x for x in gw.states(entity_id=",".join(known[r][0]["entity_id"] for r in wanted))}
            series, empty = [], []
            for role in wanted:
                eid = known[role][0]["entity_id"]
                unit = (states.get(eid, {}).get("attributes") or {}).get("unit_of_measurement") or next(
                    (u for k, u in _DEFAULT_UNITS if role.endswith(k) or role == k), "")
                points = pull(eid)
                (series if points else empty).append({"role": role, "entity_id": eid, "unit": unit, "points": points})
            if not series:
                return "No numeric readings in that window for: " + ", ".join(wanted)
            bands = {}
            if any(s["role"] == "greenhouse_temperature" for s in series) and "target_temp_low" in known and "target_temp_high" in known:
                low, high = pull(known["target_temp_low"][0]["entity_id"]), pull(known["target_temp_high"][0]["entity_id"])
                unit = next(s["unit"] for s in series if s["role"] == "greenhouse_temperature")
                if low and high:
                    bands[unit] = {"low": low, "high": high}
        except GatewayError as exc:
            return f"Gateway error: {exc}"
        spec = build_chart_spec(title.strip()[:120] or f"Last {hours:g} hours", start_ts, end_ts, series, bands)
        facts = {"hours": hours, "series": {s["label"]: {**s["stats"], "unit": p["unit"]} for p in spec["panels"] for s in p["series"]},
                 "target_band_time_pct": {p["unit"]: p["band"]["stats"] for p in spec["panels"] if p["band"]},
                 "no_data_for": [e["role"] for e in empty],
                 "note": "The chart is already shown to the user. Do not describe it point by point; give the numbers that matter."}
        return HTMLResponse(content=chart_html(spec), headers={"Content-Disposition": "inline"}), facts

    def get_roles(self, include_drafts: bool = False) -> str:
        """
        Which entity does which job in this greenhouse, e.g. greenhouse_temperature, target_temp_high, mode_switch.
        Call this FIRST when you need entity ids; trust it over guesses. Roles are set by the owners in the glossary.
        :param include_drafts: Also list roles an owner has not approved yet (suggestions only).
        :return: Role name to the entities that carry it, with their meaning.
        """
        return self._run(lambda gw: gw.roles(include_drafts))

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
