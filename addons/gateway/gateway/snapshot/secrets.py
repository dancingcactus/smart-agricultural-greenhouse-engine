"""Find known secret values in anything about to be shared with the agent side."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from urllib.parse import quote

import yaml

MIN_LENGTH = 4  # shorter values (a PIN, "on") would match everywhere; they are counted, not searched
ENV_SECRETS = ("GATEWAY_API_KEY", "VM_PASSWORD", "SUPERVISOR_TOKEN", "HA_TOKEN")


class _Loose(yaml.SafeLoader):
    pass


_Loose.add_multi_constructor("!", lambda loader, suffix, node: None)


def _flatten(value, out: list[str]) -> None:
    if isinstance(value, dict):
        for v in value.values():
            _flatten(v, out)
    elif isinstance(value, list):
        for v in value:
            _flatten(v, out)
    elif value is not None and not isinstance(value, bool):
        out.append(str(value))


def load_secret_values(config_dir: Path) -> list[str]:
    path = config_dir / "secrets.yaml"
    if not path.is_file():
        return []
    values: list[str] = []
    _flatten(yaml.load(path.read_text(errors="replace"), Loader=_Loose), values)
    return values


def needles(values: list[str]) -> tuple[list[str], int]:
    """Searchable needles (with URL-encoded variants) and how many values were too short to search."""
    found: set[str] = set()
    short = 0
    for value in {v.strip() for v in values if v and v.strip()}:
        if len(value) < MIN_LENGTH:
            short += 1
            continue
        found.update({value, quote(value, safe=""), quote(value)})
    return sorted(found), short


def env_values() -> list[str]:
    return [os.environ[name] for name in ENV_SECRETS if os.environ.get(name)]


def scan(roots: list[Path], search_for: list[str]) -> list[dict]:
    """Return {path, needle_id} for each hit. The secret itself is never returned or logged."""
    if not search_for:
        return []
    encoded = [(n, n.encode()) for n in search_for]
    hits = []
    for root in roots:
        files = [root] if root.is_file() else (p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts)
        for path in files:
            data = path.read_bytes()
            for needle, raw in encoded:
                if raw in data:
                    hits.append({"path": str(path.relative_to(root.parent if root.is_file() else root)),
                                 "needle_id": hashlib.sha256(needle.encode()).hexdigest()[:8]})
    return hits
