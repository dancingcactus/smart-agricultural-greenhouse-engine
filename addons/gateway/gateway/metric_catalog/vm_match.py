"""VictoriaMetrics side: discover how each entity is stored rather than guessing."""

from __future__ import annotations

import time
from collections import defaultdict

import httpx

from .models import EntityRecord, SeriesRecord


def detect_ingest_mode(label_names: set[str], metric_names: list[str]) -> str:
    if "entity" in label_names and any(m.startswith("homeassistant_") for m in metric_names):
        return "prometheus"
    if "entity_id" in label_names:
        return "influxdb"
    return "unknown"


def entity_id_from_labels(labels: dict[str, str]) -> str | None:
    if labels.get("entity"):
        return labels["entity"]
    eid = labels.get("entity_id")
    if not eid:
        return None
    if "." in eid:
        return eid
    return f"{labels['domain']}.{eid}" if labels.get("domain") else None


def selector_for(metric: str, labels: dict[str, str]) -> str:
    key = "entity" if "entity" in labels else "entity_id"
    parts = [f'__name__="{metric}"', f'{key}="{labels[key]}"']
    if key == "entity_id" and labels.get("domain"):
        parts.append(f'domain="{labels["domain"]}"')
    return "{" + ",".join(parts) + "}"


class VMClient:
    def __init__(self, base_url: str, timeout: float = 60.0):
        self._http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def _get(self, path: str, params: dict) -> list | dict:
        resp = self._http.get(path, params=params)
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            raise RuntimeError(f"VictoriaMetrics {path} failed: {body}")
        return body["data"]

    def labels(self, start: float) -> set[str]:
        return set(self._get("/api/v1/labels", {"start": start}))

    def series(self, start: float) -> list[dict]:
        return self._get("/api/v1/series", {"match[]": '{__name__=~".+"}', "start": start})

    def instant(self, query: str, at: float) -> list[dict]:
        return self._get("/api/v1/query", {"query": query, "time": at})["result"]


def _stat_map(vm: VMClient, fn: str, metric: str, window_s: int, at: float) -> dict[str, float]:
    query = f'{fn}({{__name__="{metric}"}}[{window_s}s])'
    out = {}
    for row in vm.instant(query, at):
        labels = {k: v for k, v in row["metric"].items() if k != "__name__"}
        out[tuple(sorted(labels.items()))] = float(row["value"][1])
    return out


def match_series(
    vm: VMClient, entities: dict[str, EntityRecord], lookback_days: int = 30, now: float | None = None
) -> tuple[str, list[SeriesRecord]]:
    """Return (ingest_mode, series). Mutates entities[*].vm_status and adds orphans."""
    now = now or time.time()
    start = now - lookback_days * 86400
    raw = vm.series(start)
    mode = detect_ingest_mode(vm.labels(start), sorted({s["__name__"] for s in raw}))

    series: list[SeriesRecord] = []
    for s in raw:
        eid = entity_id_from_labels(s)
        if eid is None:
            continue
        metric = s["__name__"]
        labels = {k: v for k, v in s.items() if k != "__name__"}
        series.append(SeriesRecord(eid, metric, labels, selector_for(metric, s)))

    window = lookback_days * 86400
    by_metric: dict[str, list[SeriesRecord]] = defaultdict(list)
    for rec in series:
        by_metric[rec.metric].append(rec)
    for metric, recs in by_metric.items():
        counts = _stat_map(vm, "count_over_time", metric, window, now)
        firsts = _stat_map(vm, "tfirst_over_time", metric, window, now)
        lasts = _stat_map(vm, "tlast_over_time", metric, window, now)
        for rec in recs:
            key = tuple(sorted(rec.labels.items()))
            rec.samples = int(counts[key]) if key in counts else None
            rec.first_seen, rec.last_seen = firsts.get(key), lasts.get(key)
            if rec.samples and rec.samples > 1 and rec.first_seen and rec.last_seen:
                rec.avg_interval_s = (rec.last_seen - rec.first_seen) / (rec.samples - 1)

    seen = {rec.entity_id for rec in series}
    for eid, ent in entities.items():
        ent.vm_status = "ok" if eid in seen else "missing"
    for eid in sorted(seen - set(entities)):
        entities[eid] = EntityRecord(entity_id=eid, vm_status="orphan")
    return mode, series
