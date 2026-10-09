"""Choose which Home Assistant config files may be mirrored, and strip secrets from the copies."""

from __future__ import annotations

import json
import re
import shutil
from fnmatch import fnmatchcase
from pathlib import Path

from gateway.redact import redact

MAX_FILE_BYTES = 2_000_000

# Only these are ever copied. Everything else, including most of .storage, is ignored.
ALLOW = (
    "*.yaml",
    "packages/**/*.yaml", "automations/**/*.yaml", "scripts/**/*.yaml", "scenes/**/*.yaml",
    "templates/**/*.yaml", "blueprints/**/*.yaml",
    ".storage/input_number", ".storage/input_boolean", ".storage/input_select", ".storage/input_datetime",
    ".storage/input_text", ".storage/input_button", ".storage/counter", ".storage/timer",
    ".storage/lovelace", ".storage/lovelace.*", ".storage/lovelace_dashboards",
)
# Refused even if an allow pattern (now or after an edit) would match. Checked first.
DENY = (
    "secrets.yaml", "*/secrets.yaml", "known_devices.yaml",
    ".storage/auth*", ".storage/core.config_entries", ".storage/application_credentials",
    ".storage/onboarding", ".storage/cloud*", ".storage/*credential*", ".storage/*token*",
    "*.pem", "*.key", "*.crt", "*.p12", "*.pfx", ".ssh/*", ".cloud/*", "*token*", "*credential*",
)
SENSITIVE_YAML_KEY = re.compile(
    r"(pass(?:word|wd|phrase)?|token|secret|api[_-]?key|access[_-]?key|private[_-]?key|credential|"
    r"authorization|bearer|webhook[_-]?id|latitude|longitude)", re.IGNORECASE)
_KEY_LINE = re.compile(r"^(?P<indent>\s*(?:-\s+)*)(?P<key>[\w.\-]+)\s*:\s*(?P<value>\S.*)?$")
_PASSTHROUGH = ("!secret", "!include", "!env_var", "!input")
_BLOCK = ("|", ">", "|-", ">-", "|+", ">+")
REDACTED = "[redacted]"


def is_denied(rel: str) -> bool:
    rel = rel.replace("\\", "/").lower()
    return any(fnmatchcase(rel, pat) for pat in DENY)


def is_allowed(rel: str) -> bool:
    rel = rel.replace("\\", "/")
    if is_denied(rel):
        return False
    for pattern in ALLOW:
        if "**" in pattern:
            if _glob_match(rel, pattern):
                return True
        elif pattern == "*.yaml":
            if "/" not in rel and fnmatchcase(rel, pattern):  # only files directly in the config directory
                return True
        elif fnmatchcase(rel, pattern):
            return True
    return False


def _glob_match(rel: str, pattern: str) -> bool:
    head, _, tail = pattern.partition("/**/")
    return rel.startswith(head + "/") and fnmatchcase(rel.rsplit("/", 1)[-1], tail)


def redact_yaml(text: str) -> tuple[str, int]:
    """Blank values of sensitive keys line by line. `!secret name` references stay: they hold no value."""
    out: list[str] = []
    count = 0
    block_indent: int | None = None
    for line in text.splitlines():
        stripped = line.lstrip()
        if block_indent is not None:
            if stripped and len(line) - len(stripped) > block_indent:
                out.append(" " * (len(line) - len(stripped)) + REDACTED)
                continue
            block_indent = None
        match = _KEY_LINE.match(line)
        value = (match.group("value") or "").strip() if match else ""
        if match and value and SENSITIVE_YAML_KEY.search(match.group("key")) \
                and not value.startswith(_PASSTHROUGH):
            out.append(f'{match.group("indent")}{match.group("key")}: "{REDACTED}"' if value not in _BLOCK
                       else f'{match.group("indent")}{match.group("key")}: {value}')
            count += 1
            if value in _BLOCK:
                block_indent = len(match.group("indent"))
        else:
            out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else ""), count


def redact_json(text: str) -> tuple[str, int]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return REDACTED + "\n", 1  # unparseable: refuse to mirror its contents
    cleaned = redact(data)
    return json.dumps(cleaned, indent=1, sort_keys=True) + "\n", int(cleaned != data)


def copy_allowlisted(config_dir: Path, staging: Path) -> list[dict]:
    """Copy allowed files into `staging`, redacted. Returns what was copied and what was skipped."""
    root = config_dir.resolve()
    report: list[dict] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/") or not is_allowed(rel):
            continue
        if not path.resolve().is_relative_to(root):
            report.append({"path": rel, "skipped": "resolves outside the config directory"})
            continue
        if path.stat().st_size > MAX_FILE_BYTES:
            report.append({"path": rel, "skipped": "larger than 2 MB"})
            continue
        text = path.read_text(errors="replace")
        cleaned, hits = redact_json(text) if rel.startswith(".storage/") else redact_yaml(text)
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(cleaned)
        report.append({"path": rel, "redactions": hits})
    return report


def clear_directory(path: Path) -> None:
    for child in path.iterdir():
        if child.name == ".git":
            continue
        shutil.rmtree(child) if child.is_dir() else child.unlink()
