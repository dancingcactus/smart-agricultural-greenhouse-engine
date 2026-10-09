"""One snapshot: copy + redact, build catalogs, scan for secrets, and only then commit."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from gateway import config

from . import automations, files, gitmirror, secrets

_lock = threading.Lock()
CONFIG_DIR_CANDIDATES = ("/homeassistant", "/config")


class SecretFound(RuntimeError):
    def __init__(self, hits: list[dict]):
        super().__init__(f"{len(hits)} secret value(s) found in files that would be shared; nothing was committed")
        self.hits = hits


def find_config_dir() -> Path:
    explicit = os.environ.get("HA_CONFIG_DIR")
    for candidate in ([explicit] if explicit else CONFIG_DIR_CANDIDATES):
        if candidate and (Path(candidate) / "configuration.yaml").is_file():
            return Path(candidate)
    raise RuntimeError("Home Assistant config directory not found (looked for configuration.yaml in "
                       f"{explicit or ', '.join(CONFIG_DIR_CANDIDATES)}); is the config folder mapped into the add-on?")


def status_path(mirror_dir: Path) -> Path:
    """Kept beside the mirror (not inside it), so the status is never part of the shared history."""
    return mirror_dir.parent / "snapshot_status.json"


def read_status(mirror_dir: Path) -> dict:
    path = status_path(mirror_dir)
    return json.loads(path.read_text()) if path.is_file() else {}


def _write_status(mirror_dir: Path, result: dict) -> None:
    status_path(mirror_dir).parent.mkdir(parents=True, exist_ok=True)
    status_path(mirror_dir).write_text(json.dumps(result, indent=1, sort_keys=True))


def run(ha, config_dir: Path, mirror_dir: Path, protected: list[str], extra_scan_roots: list[Path] | None = None,
        now: float | None = None) -> dict:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("a snapshot is already running")
    now = now or time.time()
    mirror_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="staging-", dir=mirror_dir.parent))
    try:
        report = files.copy_allowlisted(config_dir, staging)
        copied = [r for r in report if "skipped" not in r]

        label_names = {x["label_id"]: x.get("name", x["label_id"]) for x in ha.ws_list("config/label_registry/list")}
        built = automations.build(staging, ha.states(), ha.ws_list("config/entity_registry/list"), label_names, protected)
        catalog = staging / "catalog"
        catalog.mkdir(exist_ok=True)
        (catalog / "automations.json").write_text(json.dumps(built["automations"], indent=1, sort_keys=True))
        (catalog / "entity_references.json").write_text(json.dumps(built["entity_references"], indent=1, sort_keys=True))

        search_for, short = secrets.needles(secrets.load_secret_values(config_dir) + secrets.env_values())
        hits = secrets.scan([staging, *(extra_scan_roots or [])], search_for)
        if hits:
            raise SecretFound(hits)

        sha = gitmirror.sync_and_commit(staging, mirror_dir, f"config snapshot {int(now)}", now)
        result = {"ok": True, "at": now, "commit": sha or gitmirror.head(mirror_dir), "changed": sha is not None,
                  "files": len(copied), "redactions": sum(r.get("redactions", 0) for r in copied),
                  "skipped": [r for r in report if "skipped" in r], "unparsed": built["unparsed"],
                  "automations": len(built["automations"]), "secrets_checked": len(search_for),
                  "short_secrets_not_searched": short}
    except SecretFound as exc:
        result = {"ok": False, "at": now, "error": str(exc), "hits": exc.hits}
        _write_status(mirror_dir, result)
        raise
    except Exception as exc:
        _write_status(mirror_dir, {"ok": False, "at": now, "error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        _lock.release()
    _write_status(mirror_dir, result)
    return result


def run_from_env() -> dict:
    from gateway import deps

    roots = [Path(p) for p in (os.environ.get("CATALOG_OUT_DIR"),) if p and Path(p).exists()]
    return run(deps.get_ha(), find_config_dir(), config.mirror_dir(), config.protected_automations(), roots)
