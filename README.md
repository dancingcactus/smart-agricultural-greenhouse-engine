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
