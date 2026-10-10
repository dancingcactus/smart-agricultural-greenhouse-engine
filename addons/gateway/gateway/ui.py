"""The Greenhouse panel shown in the Home Assistant sidebar (through ingress)."""

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Greenhouse glossary</title>
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
.chip.rejected { color:var(--bad); } textarea { width:100%; min-height:62px; resize:vertical; } .row { display:flex;
        gap:8px; flex-wrap:wrap; margin-top:8px; align-items:center; } .row input { flex:1; min-width:160px; }
.small { color:var(--muted); font-size:.8rem; } .msg { margin-left:auto; }
details { margin:6px 0; font-size:.88rem; } summary { cursor:pointer; color:var(--muted); }
details ul { margin:4px 0 0; padding-left:20px; } details li { margin:1px 0; }
</style></head><body>
<h1>Entity glossary</h1>
<p class="sub">Say, in your own words, what each entity is and why it matters. Approved entries are what the
agent is told to trust; drafts are only suggestions.</p>
<div id="banner" class="banner" hidden>You opened this page outside Home Assistant, so it is read-only. Open it from the
Greenhouse item in the Home Assistant sidebar to approve or edit entries.</div>
<div class="bar"><input id="q" type="search" placeholder="Filter by name, id or meaning" aria-label="Filter">
<select id="status" aria-label="Status"><option value="draft">Needs review</option><option value="approved">Approved</option>
<option value="rejected">Rejected</option><option value="">All</option></select><span id="count" class="small"></span></div>
<div id="list" aria-live="polite"></div>
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
(async () => {
  try { owner = (await api("glossary/whoami")).owner; } catch (err) { owner = false; }
  $("banner").hidden = owner;
  $("q").oninput = () => { clearTimeout(window.t); window.t = setTimeout(load, 250); }; $("status").onchange = load;
  load();
})();
</script></body></html>"""
