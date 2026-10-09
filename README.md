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
