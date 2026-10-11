"""The Greenhouse panel shown in the Home Assistant sidebar (through ingress)."""

_BASE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>SAGE glossary</title>
<style>
:root { --bg:#fff; --fg:#1d2327; --muted:#5d6770; --line:#d9dee3; --card:#f6f8fa; --accent:#2f7d4f; --warn:#9a5b00;
        --bad:#a12b2b; --chip:#e8eef2; }
@media (prefers-color-scheme: dark) { :root { --bg:#14181b; --fg:#e6eaed; --muted:#9aa5ae; --line:#2b333a;
        --card:#1b2126; --accent:#58b985; --warn:#e2a64b; --bad:#e07878; --chip:#26303a; } }
* { box-sizing:border-box; } body { margin:0; padding:16px; background:var(--bg); color:var(--fg);
        font:15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; max-width:1100px; margin-inline:auto; }
h1 { font-size:1.3rem; margin:0 0 4px; } p.sub { color:var(--muted); margin:0 0 14px; }
.bar { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; } input, select, textarea, button {
        font:inherit; color:inherit; background:var(--card); border:1px solid var(--line); border-radius:6px; padding:6px 9px; }
button { cursor:pointer; } button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button:disabled { opacity:.5; cursor:not-allowed; } .banner { padding:10px 12px; border-radius:6px; margin-bottom:12px;
        background:var(--card); border:1px solid var(--warn); color:var(--warn); }
.entry { border:1px solid var(--line); border-radius:8px; padding:12px; margin-bottom:10px; background:var(--card); }
.entry h2 { font-size:1rem; margin:0; } code { font-family:ui-monospace, Menlo, monospace; font-size:.85rem; color:var(--muted); }
.meta { display:flex; flex-wrap:wrap; gap:6px; margin:6px 0 8px; } .chip { background:var(--chip); border-radius:99px;
        padding:1px 9px; font-size:.78rem; color:var(--muted); } .chip.approved { color:var(--accent); }
.chip.rejected { color:var(--bad); } .chip.role { color:var(--accent); border:1px solid var(--accent); background:transparent; } textarea { width:100%; min-height:62px; resize:vertical; } .row { display:flex;
        gap:8px; flex-wrap:wrap; margin-top:8px; align-items:center; } .row input { flex:1; min-width:160px; }
.small { color:var(--muted); font-size:.8rem; } .msg { margin-left:auto; }
details { margin:6px 0; font-size:.88rem; } summary { cursor:pointer; color:var(--muted); }
details ul { margin:4px 0 0; padding-left:20px; } details li { margin:1px 0; }
/*COST_CSS*/
</style></head><body>
<nav class="tabs" role="tablist" aria-label="Panel sections"><button id="tab-glossary" role="tab" aria-selected="true">Glossary</button><button id="tab-cost" role="tab" aria-selected="false">Cost</button></nav>
<section id="pane-glossary">
<h1>Entity glossary</h1>
<p class="sub">Say, in your own words, what each entity is and why it matters. Approved entries are what the
agent is told to trust; drafts are only suggestions. An alias such as <code>role:greenhouse_temperature</code>
names the job an entity does, so the agent and the watch can find it.</p>
<div id="banner" class="banner" hidden>You opened this page outside Home Assistant, so it is read-only. Open it from the
Greenhouse item in the Home Assistant sidebar to approve or edit entries.</div>
<details id="importer" class="entry"><summary>Import roles</summary>
<p class="small">One role per line as <code>role_name: entity_id</code> (several entities separated by commas). Lines starting
with # are ignored. Each becomes a draft with a <code>role:</code> alias; approve it below.</p>
<textarea id="roletext" rows="8" placeholder="greenhouse_temperature: sensor.temp5"></textarea>
<div class="row"><button id="roleimport" class="primary">Import as drafts</button><span id="roleresult" class="small"></span></div>
<pre id="roledetail" class="small" hidden></pre></details>
<div class="bar"><input id="q" type="search" placeholder="Filter by name, id or meaning" aria-label="Filter">
<select id="status" aria-label="Status"><option value="draft">Needs review</option><option value="approved">Approved</option>
<option value="rejected">Rejected</option><option value="">All</option></select><span id="count" class="small"></span></div>
<div id="list" aria-live="polite"></div>
</section>
<!--COST_PANE-->
<script>
const $ = (id) => document.getElementById(id);
let owner = false;
async function api(path, options) {
  const r = await fetch(path, options);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}
function chip(text, cls) { const s = document.createElement("span"); s.className = "chip " + (cls || ""); s.textContent = text; return s; }
const KINDS = [["automations", "Automations"], ["scripts", "Scripts"], ["scenes", "Scenes"], ["dashboards", "Dashboards"]];
function usageBlock(e) {
  const wrap = document.createElement("div");
  const total = KINDS.reduce((n, [k]) => n + (e.usage[k] || []).length, 0);
  if (total) {
    const d = document.createElement("details"); const s = document.createElement("summary");
    s.textContent = "Used in " + total + " place" + (total === 1 ? "" : "s"); d.append(s);
    for (const [k, label] of KINDS) {
      if (!(e.usage[k] || []).length) continue;
      const h = document.createElement("div"); h.className = "small"; h.textContent = label; d.append(h);
      const ul = document.createElement("ul");
      for (const u of e.usage[k]) { const li = document.createElement("li"); li.textContent = u.name; ul.append(li); }
      d.append(ul);
    }
    wrap.append(d);
  }
  if (e.same_device.length) {
    const d = document.createElement("details"); const s = document.createElement("summary");
    s.textContent = "Same device: " + e.same_device.length + " other entit" + (e.same_device.length === 1 ? "y" : "ies"); d.append(s);
    const ul = document.createElement("ul");
    for (const id of e.same_device) { const li = document.createElement("li"); li.textContent = id; ul.append(li); }
    d.append(ul); wrap.append(d);
  }
  return wrap;
}
function render(entries) {
  const list = $("list"); list.replaceChildren();
  $("count").textContent = entries.length + " shown";
  for (const e of entries) {
    const card = document.createElement("div"); card.className = "entry";
    const h = document.createElement("h2"); h.textContent = e.friendly_name || e.entity_id;
    const id = document.createElement("code"); id.textContent = e.entity_id;
    const meta = document.createElement("div"); meta.className = "meta";
    meta.append(chip(e.status, e.status));
    if (e.unit) meta.append(chip(e.unit)); if (e.area) meta.append(chip(e.area));
    if (e.device_class) meta.append(chip(e.device_class));
    if (e.device_name) meta.append(chip("device: " + e.device_name + (e.device_model ? " (" + e.device_model + ")" : "")));
    else if (e.platform) meta.append(chip(e.platform));
    for (const a of e.aliases) if (a.startsWith("role:")) meta.append(chip("role: " + a.slice(5), "role"));
    if (e.cryptic >= 0.5) meta.append(chip("name may be cryptic"));
    if (e.approved_by) meta.append(chip("approved by " + e.approved_by));
    const text = document.createElement("textarea"); text.value = e.meaning || ""; text.disabled = !owner;
    text.setAttribute("aria-label", "Meaning of " + e.entity_id); text.maxLength = 600;
    const aliases = document.createElement("input"); aliases.value = e.aliases.join(", "); aliases.disabled = !owner;
    aliases.placeholder = "Other names people use, comma separated"; aliases.setAttribute("aria-label", "Aliases");
    const msg = document.createElement("span"); msg.className = "msg small";
    const save = async (status) => {
      msg.textContent = "Saving…";
      try {
        await api("glossary/" + encodeURIComponent(e.entity_id), { method: "PUT", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ meaning: text.value, aliases: aliases.value.split(",").map((a) => a.trim()).filter(Boolean), status }) });
        msg.textContent = "Saved"; load();
      } catch (err) { msg.textContent = err.message; }
    };
    const row = document.createElement("div"); row.className = "row";
    for (const [label, status, cls] of [["Approve", "approved", "primary"], ["Save as draft", "draft", ""], ["Reject", "rejected", ""]]) {
      const b = document.createElement("button"); b.textContent = label; b.className = cls; b.disabled = !owner;
      b.onclick = () => save(status); row.append(b);
    }
    row.append(aliases, msg);
    const top = document.createElement("div"); top.append(h, id);
    card.append(top, meta, usageBlock(e), text, row); list.append(card);
  }
  if (!entries.length) list.textContent = "Nothing here.";
}
async function load() {
  const p = new URLSearchParams({ q: $("q").value }); if ($("status").value) p.set("status", $("status").value);
  try { render(await api("glossary?" + p)); } catch (err) { $("list").textContent = "Could not load: " + err.message; }
}
function parseRoles(text) {
  const roles = {};
  for (const raw of text.split("\\n")) {
    const line = raw.trim(); if (!line || line.startsWith("#")) continue;
    const i = line.indexOf(":"); if (i < 1) continue;
    const ids = line.slice(i + 1).split(",").map((x) => x.trim()).filter(Boolean);
    if (ids.length) roles[line.slice(0, i).trim()] = ids;
  }
  return roles;
}
async function importRoles() {
  const roles = parseRoles($("roletext").value);
  if (!Object.keys(roles).length) { $("roleresult").textContent = "Nothing to import."; return; }
  $("roleresult").textContent = "Importing…";
  try {
    const out = await api("glossary/roles", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ roles }) });
    const n = (k) => out.filter((r) => r.result === k).length;
    $("roleresult").textContent = n("drafted") + " drafted, " + n("skipped") + " skipped, " + n("error") + " problems";
    const bad = out.filter((r) => r.result !== "drafted");
    $("roledetail").hidden = !bad.length;
    $("roledetail").textContent = bad.map((r) => (r.entity_id || r.role) + ": " + r.detail).join("\\n");
    $("status").value = "draft"; load();
  } catch (err) { $("roleresult").textContent = err.message; }
}
(async () => {
  try { owner = (await api("glossary/whoami")).owner; } catch (err) { owner = false; }
  $("banner").hidden = owner; $("roleimport").onclick = importRoles; $("roleimport").disabled = !owner;
  $("q").oninput = () => { clearTimeout(window.t); window.t = setTimeout(load, 250); }; $("status").onchange = load;
  load();
})();
</script>
<!--COST_SCRIPT-->
</body></html>"""


COST_CSS = r"""
:root { --s1:#2a78d6; --s2:#eb6834; --s3:#1baf7a; --s4:#eda100; --sx:#8a949c; }
@media (prefers-color-scheme: dark) { :root { --s1:#3987e5; --s2:#d95926; --s3:#199e70; --s4:#c98500; --sx:#6f7a83; } }
.tabs { display:flex; gap:4px; margin-bottom:14px; border-bottom:1px solid var(--line); }
.tabs button { border:0; border-bottom:2px solid transparent; border-radius:0; background:none; padding:8px 14px; color:var(--muted); }
.tabs button[aria-selected="true"] { color:var(--fg); border-bottom-color:var(--accent); font-weight:600; }
.range { display:flex; gap:6px; margin-bottom:14px; align-items:center; flex-wrap:wrap; }
.range button[aria-pressed="true"] { background:var(--accent); border-color:var(--accent); color:#fff; }
.tiles { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:10px; margin-bottom:16px; }
.tile { border:1px solid var(--line); border-radius:8px; padding:10px 12px; background:var(--card); }
.tile .label { color:var(--muted); font-size:.8rem; } .tile .value { font-size:1.5rem; font-weight:600; }
.tile .note { color:var(--muted); font-size:.75rem; }
.chartcard { border:1px solid var(--line); border-radius:8px; padding:12px; background:var(--card); margin-bottom:16px; }
.chartcard h2 { font-size:1rem; margin:0 0 2px; } .legend { display:flex; gap:14px; flex-wrap:wrap; margin:8px 0; font-size:.85rem; }
.legend span { display:inline-flex; align-items:center; gap:6px; } .legend i { width:10px; height:10px; border-radius:2px; display:inline-block; }
.plotwrap { position:relative; height:220px; margin-left:46px; margin-top:8px; }
.gridline { position:absolute; left:0; right:0; border-top:1px solid var(--line); } .gridline b { position:absolute; left:-46px; top:-.65em;
  width:40px; text-align:right; font-weight:400; font-size:.75rem; color:var(--muted); }
.cols { position:absolute; inset:0; display:flex; align-items:flex-end; }
.col { flex:1; height:100%; display:flex; justify-content:center; align-items:flex-end; outline-offset:-2px; position:relative; }
.col:hover, .col:focus-visible { background:var(--chip); }
.stack { width:min(24px, 70%); display:flex; flex-direction:column-reverse; }
.seg { margin-top:2px; min-height:2px; } .seg.top { border-radius:4px 4px 0 0; }
.xlabels { display:flex; margin-left:46px; } .xlabels span { flex:1; text-align:center; font-size:.72rem; color:var(--muted); min-height:1em; white-space:nowrap; overflow:visible; }
.tip { position:absolute; z-index:5; pointer-events:none; background:var(--bg); border:1px solid var(--line); border-radius:6px;
  padding:7px 10px; font-size:.82rem; box-shadow:0 2px 8px rgba(0,0,0,.18); min-width:150px; }
.tip .t { color:var(--muted); font-size:.75rem; margin-bottom:3px; } .tip .r { display:flex; align-items:center; gap:6px; justify-content:space-between; }
.tip .r i { width:14px; height:0; border-top:3px solid; display:inline-block; } .tip .r b { margin-left:auto; font-weight:600; }
.tip .r span { color:var(--muted); display:inline-flex; align-items:center; gap:6px; }
table.data { width:100%; border-collapse:collapse; font-size:.85rem; margin-top:6px; } table.data th, table.data td {
  text-align:right; padding:4px 8px; border-bottom:1px solid var(--line); } table.data th:first-child, table.data td:first-child { text-align:left; }
table.data td.num { font-variant-numeric:tabular-nums; } table.data th, table.data td { white-space:nowrap; } .scroll { overflow-x:auto; } .dim { opacity:.55; transition:opacity .15s; }
"""

COST_PANE = """<section id="pane-cost" hidden>
<h1>Cost</h1>
<p class="sub">What the SAGE models have cost, from the run ledger. Costs come from OpenRouter with each answer; a call
with no cost reported is counted but not priced. Days follow your local time.</p>
<div class="range" role="group" aria-label="Date range"><button data-days="7">Last 7 days</button>
<button data-days="30" aria-pressed="true">Last 30 days</button><button data-days="90">Last 90 days</button>
<span id="cost-status" class="small"></span></div>
<div id="cost-body">
<div class="tiles" id="tiles"></div>
<div class="chartcard"><h2>Cost per day</h2><div class="small">Stacked by model role</div>
<div class="legend" id="legend" aria-label="Legend"></div>
<div class="plotwrap" id="plot"></div><div class="xlabels" id="xlabels"></div></div>
<div class="chartcard"><h2>By role</h2><div class="scroll"><table class="data" id="roletable"></table></div>
<details><summary>Table view of the chart (cost per day and role)</summary><div class="scroll"><table class="data" id="daytable"></table></div></details></div>
<div class="chartcard"><h2>Recent calls</h2><div class="scroll"><table class="data" id="recent"></table></div></div>
</div></section>"""

COST_JS = r"""<script>
(() => {
  const el = (id) => document.getElementById(id);
  const ROLES = [["small", "Small", "var(--s1)"], ["mid", "Analyst", "var(--s2)"], ["builder", "Builder", "var(--s3)"],
                 ["reviewer", "Reviewer", "var(--s4)"]];
  const OTHER = ["other", "Other", "var(--sx)"];
  let days = 30, loaded = false;
  const money = (v) => !v ? "$0" : v >= 0.1 ? "$" + v.toFixed(2) : v >= 0.01 ? "$" + v.toFixed(3) : "$" + v.toFixed(4);
  const num = (v) => v >= 1e6 ? (v / 1e6).toFixed(1) + "M" : v >= 1e3 ? (v / 1e3).toFixed(1) + "K" : String(Math.round(v || 0));
  const roleInfo = (r) => ROLES.find((x) => x[0] === r) || OTHER;
  const make = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
  const cell = (row, text, isNum, head) => { const c = make(head ? "th" : "td", isNum ? "num" : "", text); row.append(c); return c; };
  function niceMax(v) {
    for (const s of [0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]) if (v <= s) return s;
    return Math.ceil(v / 100) * 100;
  }
  async function fetchJson(path) {
    const r = await fetch(path); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  }
  function table(id, head, rows) {
    const t = el(id); t.replaceChildren();
    const h = make("tr"); head.forEach((x, i) => cell(h, x, i > 0, true)); t.append(h);
    for (const r of rows) { const tr = make("tr"); r.forEach((x, i) => cell(tr, x, i > 0 && typeof x !== "object")); t.append(tr); }
  }
  function tile(label, value, note) {
    const t = make("div", "tile"); t.append(make("div", "label", label), make("div", "value", value));
    if (note) t.append(make("div", "note", note)); return t;
  }
  function render(daily, recent) {
    const byDay = new Map(daily.days.map((d) => [d, {}]));
    const roleTotals = {}; let unpriced = 0, calls = 0;
    for (const r of daily.rows) {
      const info = roleInfo(r.role)[0];
      const day = byDay.get(r.day); if (!day) continue;
      const slot = day[info] || (day[info] = { cost: 0, calls: 0 });
      slot.cost += r.cost_usd; slot.calls += r.calls;
      const tot = roleTotals[r.role] || (roleTotals[r.role] = { calls: 0, pin: 0, pout: 0, cost: 0, unpriced: 0 });
      tot.calls += r.calls; tot.pin += r.prompt_tokens; tot.pout += r.completion_tokens; tot.cost += r.cost_usd; tot.unpriced += r.unpriced || 0;
      unpriced += r.unpriced || 0; calls += r.calls;
    }
    const totalOf = (d) => Object.values(byDay.get(d)).reduce((a, x) => a + x.cost, 0);
    const spend = daily.days.reduce((a, d) => a + totalOf(d), 0);
    const today = totalOf(daily.days[daily.days.length - 1]);
    const avg = spend / daily.days.length;
    const tiles = el("tiles"); tiles.replaceChildren(
      tile("Today", money(today)), tile("Last " + daily.days.length + " days", money(spend), calls + " calls"),
      tile("Average per day", money(avg)), tile("Projected 30 days", money(avg * 30), "at this pace; the plan assumed about $5 to $10 a month"));
    if (unpriced) tiles.append(tile("Calls without a cost", String(unpriced), "counted, not priced"));

    // legend (always shown: more than one series)
    const legend = el("legend"); legend.replaceChildren();
    for (const [, name, color] of ROLES) { const s = make("span"); const i = make("i"); i.style.background = color; s.append(i, document.createTextNode(name)); legend.append(s); }

    // chart
    const max = niceMax(Math.max(0, ...daily.days.map(totalOf)));
    const plot = el("plot"); plot.replaceChildren();
    for (const f of [0, 0.5, 1]) {
      const g = make("div", "gridline"); g.style.bottom = f * 100 + "%"; g.append(make("b", "", money(max * f))); plot.append(g);
    }
    const cols = make("div", "cols"); plot.append(cols);
    const tip = make("div", "tip"); tip.hidden = true; plot.append(tip);
    const showTip = (col, d) => {
      tip.replaceChildren(make("div", "t", d + (totalOf(d) ? "" : " (no cost)")));
      for (const [key, name, color] of [...ROLES, OTHER]) {
        const v = byDay.get(d)[key]; if (!v) continue;
        const row = make("div", "r"); const left = make("span"); const k = make("i"); k.style.borderTopColor = color;
        left.append(k, document.createTextNode(name)); row.append(left, make("b", "", money(v.cost))); tip.append(row);
      }
      const sum = make("div", "r"); sum.append(make("span", "", "Total"), make("b", "", money(totalOf(d)))); tip.append(sum);
      tip.hidden = false;
      const left = Math.min(col.offsetLeft + col.offsetWidth / 2 + 8, plot.clientWidth - tip.offsetWidth - 4);
      tip.style.left = Math.max(0, left) + "px"; tip.style.top = "4px";
    };
    daily.days.forEach((d) => {
      const col = make("div", "col"); col.tabIndex = 0; col.setAttribute("role", "img");
      col.setAttribute("aria-label", d + ": " + money(totalOf(d)));
      const stack = make("div", "stack"); const present = [...ROLES, OTHER].filter(([k]) => byDay.get(d)[k] && byDay.get(d)[k].cost > 0);
      present.forEach(([k, , color], i) => {
        const seg = make("div", "seg" + (i === present.length - 1 ? " top" : "")); seg.style.background = color;
        seg.style.height = "calc(" + (byDay.get(d)[k].cost / max * 100) + "% - 0px)"; stack.append(seg);
      });
      // segments are sized against the plot height, so wrap them in a box that is that tall
      const box = make("div"); box.style.height = "100%"; box.style.display = "flex"; box.style.alignItems = "flex-end"; box.style.width = "100%"; box.style.justifyContent = "center";
      stack.style.height = "100%"; stack.style.justifyContent = "flex-start"; box.append(stack); col.append(box);
      col.onpointerenter = col.onfocus = () => showTip(col, d);
      col.onpointerleave = col.onblur = () => { tip.hidden = true; };
      cols.append(col);
    });
    const xl = el("xlabels"); xl.replaceChildren(); const every = Math.ceil(daily.days.length / 8);
    const lastIdx = daily.days.length - 1;
    daily.days.forEach((d, i) => xl.append(make("span", "", i % every === 0 && lastIdx - i >= every / 2 || i === lastIdx ? d.slice(5) : "")));

    // tables
    const roleRows = Object.keys(roleTotals).sort().map((r) => { const t = roleTotals[r];
      return [roleInfo(r)[1], String(t.calls), num(t.pin), num(t.pout), money(t.cost), String(t.unpriced)]; });
    table("roletable", ["Role", "Calls", "Tokens in", "Tokens out", "Cost", "No cost reported"], roleRows.length ? roleRows : [["No runs in this range", "", "", "", "", ""]]);
    table("daytable", ["Day", ...ROLES.map((r) => r[1]), "Total"], daily.days.slice().reverse().map((d) =>
      [d, ...ROLES.map(([k]) => money((byDay.get(d)[k] || { cost: 0 }).cost)), money(totalOf(d))]));
    table("recent", ["Time", "Role", "Model", "Tokens in", "Tokens out", "Cost", "Prompt", "Status"], recent.length ? recent.map((r) => [
      new Date(r.ts * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }),
      roleInfo(r.role)[1], r.model || "", num(r.prompt_tokens), num(r.completion_tokens),
      r.cost_usd == null ? "n/a" : money(r.cost_usd), r.prompt_hash || "", r.status]) : [["No calls recorded yet. Chat with a SAGE model and they appear here.", "", "", "", "", "", "", ""]]);
  }
  async function load() {
    const body = el("cost-body"); body.classList.add("dim"); el("cost-status").textContent = "Loading…";
    try {
      const offset = -new Date().getTimezoneOffset();
      const [daily, recent] = await Promise.all([fetchJson("runs/daily?days=" + days + "&offset_minutes=" + offset), fetchJson("runs?limit=25")]);
      render(daily, recent); el("cost-status").textContent = ""; loaded = true;
    } catch (err) { el("cost-status").textContent = "Could not load: " + err.message; }
    body.classList.remove("dim");
  }
  function select(which) {
    const cost = which === "cost";
    el("pane-cost").hidden = !cost; el("pane-glossary").hidden = cost;
    el("tab-cost").setAttribute("aria-selected", String(cost)); el("tab-glossary").setAttribute("aria-selected", String(!cost));
    if (cost) load();
  }
  el("tab-glossary").onclick = () => select("glossary"); el("tab-cost").onclick = () => select("cost");
  document.querySelectorAll(".range button").forEach((b) => { b.onclick = () => {
    days = Number(b.dataset.days); document.querySelectorAll(".range button").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); load(); }; });
})();
</script>"""

PAGE = _BASE.replace("/*COST_CSS*/", COST_CSS).replace("<!--COST_PANE-->", COST_PANE).replace("<!--COST_SCRIPT-->", COST_JS)
