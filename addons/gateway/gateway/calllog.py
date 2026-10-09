"""Append-only JSONL log of every request, including refused ones."""

from __future__ import annotations

import json
import threading
from pathlib import Path


class CallLog:
    def __init__(self, path: Path, max_bytes: int = 20_000_000, backups: int = 3):
        self.path, self.max_bytes, self.backups = path, max_bytes, backups
        self._lock = threading.Lock()

    def write(self, record: dict) -> None:
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if self.path.exists() and self.path.stat().st_size > self.max_bytes:
                self._rotate()
            with self.path.open("a") as fh:
                fh.write(line)

    def _rotate(self) -> None:
        for i in range(self.backups, 0, -1):
            src = self.path if i == 1 else self.path.with_name(f"{self.path.name}.{i - 1}")
            if src.exists():
                src.replace(self.path.with_name(f"{self.path.name}.{i}"))

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open() as fh:
            return [json.loads(line) for line in fh if line.strip()]
