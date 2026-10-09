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
