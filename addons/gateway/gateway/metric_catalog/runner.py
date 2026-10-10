from __future__ import annotations

import os
import subprocess
from pathlib import Path

from gateway.ha_client import HAClient

from . import store
from .collect import collect_entities
from .vm_match import VMClient, match_series


def run_once(ha: HAClient, vm: VMClient, db_path: str | Path, out_dir: str | Path | None = None,
             lookback_days: int = 365, git_commit: bool = False, history_days: int = 1100,
             ignore: list[str] | None = None) -> dict:
    entities = collect_entities(ha)
    mode, series = match_series(vm, entities, lookback_days, history_days=history_days, ignore=ignore)
    conn = store.connect(db_path)
    try:
        run_id = store.save_run(conn, mode, entities, series)
        if out_dir:
            store.export(conn, out_dir)
            if git_commit:
                _commit(Path(out_dir), run_id)
        return {"run_id": run_id, "ingest_mode": mode, "entities": len(entities), "series": len(series),
                "changes": {k: len(v) for k, v in store.diff_runs(conn).items()}}
    finally:
        conn.close()


def _commit(out_dir: Path, run_id: int) -> None:
    """Commit exported catalog files if out_dir is inside a git repo; a no-op diff is fine."""
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(out_dir), *args], capture_output=True, text=True, check=False)

    if git("rev-parse", "--is-inside-work-tree").returncode != 0:
        return
    git("add", "entity_metrics.jsonl", "entity_metrics.csv")
    git("-c", "user.name=greenhouse-gateway", "-c", "user.email=gateway@localhost",
        "commit", "-m", f"metric catalog run {run_id}", "--", "entity_metrics.jsonl", "entity_metrics.csv")


def config_from_env() -> dict:
    return {
        "db_path": os.environ.get("CATALOG_DB", "metric_catalog.sqlite3"),
        "out_dir": os.environ.get("CATALOG_OUT_DIR"),
        "lookback_days": int(os.environ.get("CATALOG_LOOKBACK_DAYS", "365")),
        "ignore": [p for p in os.environ.get("CATALOG_IGNORE", "").splitlines() if p.strip()],
        "history_days": int(os.environ.get("CATALOG_HISTORY_DAYS", "1100")),
        "vm_url": os.environ.get("VM_URL", "http://localhost:8428"),
        "vm_username": os.environ.get("VM_USERNAME", ""),
        "vm_password": os.environ.get("VM_PASSWORD", ""),
    }


def run_from_env() -> dict:
    """Entry point for the scheduler: configuration comes from the environment."""
    cfg = config_from_env()
    result = run_once(HAClient.from_env(), _vm_client(cfg), cfg["db_path"], cfg["out_dir"],
                      cfg["lookback_days"], history_days=cfg["history_days"],
                      ignore=cfg["ignore"])
    try:  # new entities get a draft glossary entry; a failure here must not fail the catalog run
        from gateway.glossary.sync import sync_from_env

        result["glossary"] = sync_from_env()
    except Exception as exc:  # noqa: BLE001
        result["glossary"] = {"error": f"{type(exc).__name__}: {exc}"}
    return result


def _vm_client(cfg: dict) -> VMClient:
    return VMClient(cfg["vm_url"], username=cfg["vm_username"], password=cfg["vm_password"])
