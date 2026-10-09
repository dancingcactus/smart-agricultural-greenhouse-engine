# Greenhouse AI Operator

Shadow-phase LLM head grower for a Home Assistant greenhouse. See the requirements doc for scope.

## Entity → VictoriaMetrics metric catalog

```
cd addons/gateway && pip install -e '.[dev]'
export HA_URL=http://homeassistant.local:8123 HA_TOKEN=... VM_URL=http://victoria:8428
python -m gateway.metric_catalog run --out-dir ../../context/catalog --git-commit
python -m gateway.metric_catalog search "humidity"   # also: mdi:fan, a label, a metric name
python -m gateway.metric_catalog changes
```

Inside the add-on it runs on `metric_catalog_cron` (default daily 03:15). Output: SQLite (FTS5) plus
`entity_metrics.jsonl`/`.csv`. `vm_status` is `ok`, `missing` (no VM series) or `orphan` (series
with no HA entity). `avg_interval_s` is (last - first)/(samples - 1), a mean, not a median.

## Run as a Home Assistant add-on

1. Settings → Add-ons → Add-on Store → ⋮ → Repositories → add
   `https://github.com/dancingcactus/smart-agricultural-greenhouse-engine`
   (the add-on lives on the `claude/busy-wozniak-v9tggp` branch until it is merged to the default branch).
2. Install **Greenhouse Gateway**. On the Configuration tab set `vm_url` to where VictoriaMetrics is
   reachable *from the add-on* (e.g. `http://192.168.1.20:8428`; `localhost` is the add-on itself).
3. Start it and watch the **Log** tab: the first catalog run happens at startup, then daily per
   `metric_catalog_cron`.
4. To query it, enable port 8099 on the Network tab, then:
   `curl http://homeassistant.local:8099/catalog/status`,
   `.../catalog/metrics?q=humidity`, `.../catalog/export.csv`, or `curl -X POST .../catalog/run`
   to refresh on demand. Leave the port disabled when not testing: the API is unauthenticated.

Data lives in `/data` (included in Home Assistant backups). The add-on gets its Home Assistant
token from the Supervisor; no long-lived token is needed.

## Reading the catalog (InfluxDB ingest)

- Sensors with a unit are stored under the **unit**, not the entity: `W_value`, `°F_value`. Always use
  the generated `selector` (it pins `entity_id` and `domain`); a bare `°F_value` returns every
  temperature sensor.
- `kind` is `value` (the numeric reading), `attribute_num`, `attribute_str` (text attributes such as
  `friendly_name_str`, ignorable) or `state` (string state of enums and similar).
- `first_seen`/`samples` cover the lookback window only; `history_first_seen` looks back ~3 years.
- Setpoints and other write-on-change entities have few samples and a meaningless
  `avg_interval_s`; their value holds until the next change, so use a long window to find it.
- `vm_status`: `ok` (numeric data), `string_only`, `missing` (see `vm_reason`: `disabled`,
  `no_state`, `non_numeric`, `not_exported` — only the last needs attention) or `orphan`.
  `/catalog/status` returns counts by status, reason and domain, and lists the orphans.

## Read API (for the agent side)

All routes are `GET` (the only `POST` refreshes the metric catalog). Set an `api_key` in the add-on
options and send it as `X-Gateway-Key`; `/health` stays open. Optional headers: `X-As-Of` (unix
seconds or RFC3339) and `X-Caller` (which tool is calling, for the log).

- **As-of.** With `X-As-Of` (or the add-on option `as_of_override`, which callers cannot loosen),
  every query end/time is clipped to that moment. Live-only endpoints (`/ha/states`, `/ha/registry/*`,
  `/ha/services`) answer 409 instead, because they would reveal the present. Traces after the
  as-of time are hidden.
- **VictoriaMetrics**: `/vm/query`, `/vm/query_range`, `/vm/series`, `/vm/labels`,
  `/vm/label/{name}/values`. PromQL containing `@`, negative or non-literal `offset`, or negative
  lookback windows is rejected (always, so tools behave the same live and in replays). Only known
  parameters are forwarded.
- **Home Assistant**: `/ha/states[/{id}]`, `/ha/history`, `/ha/logbook`, `/ha/registry/{entity|device|area|label}`,
  `/ha/services`, `/ha/traces[/{automation_id}/{run_id}]`. Token-like attributes and `?token=` URLs are
  redacted from responses.
- **Call log**: every request, including refused ones, is appended to `/data/calls.jsonl`
  (route, as-of, caller, status, latency; query strings with secrets redacted). `/calls/summary` shows
  which resources are used and which calls were refused.

## Ignore list, long lookback and helper mirrors

- **Ignore list.** Add globs to the `catalog_ignore` option (e.g. `sensor.small_screen_*`). Matching
  entities are marked `ignored`: still searchable, but left out of the missing counts. It also
  applies to orphans.
- **Lookback** defaults to 365 days (max 1100), because step-like data such as setpoints may not
  change for months.
- **Helper mirrors.** The InfluxDB integration only writes on change, so a setpoint nobody touches
  stops producing samples and can age out of VictoriaMetrics retention. Generate template sensors
  that re-record helpers on a timer:
  ```
  curl 'http://homeassistant.local:8099/catalog/mirror.yaml?domain=input_number&only=stale&hours=6'
  ```
  (`only=all` for every helper; `hours` is 1, 2, 3, 4, 6, 8, 12 or 24.) Review the output, paste it
  under `template:` in `configuration.yaml`, run a config check and reload. Needs Home Assistant
  2024.10+. Each mirror is `sensor.<helper>_snapshot`; the catalog links it to its helper
  (`mirror_of` / `mirrored_by`) and shows the helper as `mirrored`. Mirrors carry a `recorded_at`
  attribute so every run is a change; the matching text series (`recorded_at_str`) is noise.

## Recording helpers directly into VictoriaMetrics (no extra entities)

Alternative to the mirror sensors above, and the one that keeps Home Assistant clean. With
`helper_snapshot_enabled` on, the gateway writes each listed helper's current value to
VictoriaMetrics every `helper_snapshot_cron` (default every 6 hours, and at start-up), as line
protocol on `/write`, under the series Home Assistant's InfluxDB integration already uses:
the measurement follows the integration's convention (the unit, or the entity id when there is no unit)
and the tags are `domain`, `entity_id` plus any extra tag nearly every series carries (such as
`db=homeassistant`, which VictoriaMetrics adds for Home Assistant's database). If the catalog already
holds a series for the helper under a different name, the helper is skipped instead of splitting its
history. A catalog run must exist first.

This is the gateway's only write path. It is off by default, writes only to VictoriaMetrics, only
`input_number` and `input_boolean` entities matching `helper_snapshot_entities`, and nothing in the
HTTP API can trigger it. `GET /catalog/snapshot-preview` shows exactly what would be written.
