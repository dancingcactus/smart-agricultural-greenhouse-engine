"""Scheduled pull of the configured weather entities into the forecast archive."""

from __future__ import annotations

from gateway import config, deps
from gateway.weather import archive


def run_from_env() -> dict:
    conn = archive.connect(config.weather_db())
    try:
        results, failures = [], {}
        for entity_id in config.weather_entities():
            try:
                results.append(archive.pull(deps.get_ha(), conn, entity_id,
                                            horizon_hours=config.weather_horizon_hours()))
            except (RuntimeError, OSError) as exc:
                failures[entity_id] = str(exc)
        if failures and not results:
            raise RuntimeError(f"weather pull failed: {failures}")
        return {"pulled": results, "failed": failures}
    finally:
        conn.close()
