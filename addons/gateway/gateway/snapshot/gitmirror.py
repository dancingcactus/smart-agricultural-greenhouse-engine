"""The mirror is a git repository: each passing snapshot is a commit, so config can be read as of any time."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from .files import clear_directory

IDENT = ("-c", "user.name=greenhouse-gateway", "-c", "user.email=gateway@localhost", "-c", "commit.gpgsign=false")
_SAFE_PATH = re.compile(r"^[\w .@+\-/]+$")


class GitError(RuntimeError):
    pass


def _git(repo: Path, *args: str, env: dict | None = None, text: bool = True):
    proc = subprocess.run(["git", "-C", str(repo), *IDENT, *args], capture_output=True, text=text,
                          env={**os.environ, **(env or {})}, check=False)
    if proc.returncode != 0:
        raise GitError((proc.stderr if text else proc.stderr.decode(errors="replace")).strip()[:300])
    return proc.stdout


def ensure_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        _git(repo, "init", "-q")


def sync_and_commit(staging: Path, repo: Path, message: str, when: float) -> str | None:
    """Make the repo's working tree equal `staging` and commit if anything changed (returns the new sha)."""
    import shutil

    ensure_repo(repo)
    clear_directory(repo)
    shutil.copytree(staging, repo, dirs_exist_ok=True)
    _git(repo, "add", "-A")
    if not _git(repo, "status", "--porcelain").strip():
        return None
    stamp = f"@{int(when)} +0000"
    _git(repo, "commit", "-q", "-m", message, env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
    return head(repo)


def head(repo: Path) -> str | None:
    try:
        return _git(repo, "rev-parse", "HEAD").strip()
    except GitError:
        return None  # no commits yet


def commits(repo: Path, path: str | None = None, limit: int = 200) -> list[dict]:
    if head(repo) is None:
        return []
    args = ["log", f"-{limit}", "--format=%H%x09%ct%x09%s"]
    if path:
        args += ["--", safe_path(path)]
    rows = [line.split("\t", 2) for line in _git(repo, *args).splitlines() if line]
    return [{"sha": sha, "ts": float(ts), "message": msg} for sha, ts, msg in rows]


def commit_at(repo: Path, as_of: float | None) -> str | None:
    """The latest commit made at or before `as_of` (HEAD when no time is given)."""
    if as_of is None:
        return head(repo)
    return next((c["sha"] for c in commits(repo, limit=100000) if c["ts"] <= as_of), None)


def safe_path(path: str) -> str:
    if not path or path.startswith("/") or ".." in path.split("/") or not _SAFE_PATH.match(path):
        raise ValueError("bad path")
    return path


def read_file(repo: Path, rev: str, path: str) -> bytes | None:
    try:
        out = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{safe_path(path)}"], capture_output=True,
                             check=True)
    except subprocess.CalledProcessError:
        return None
    return out.stdout


def list_files(repo: Path, rev: str) -> list[str]:
    return _git(repo, "ls-tree", "-r", "--name-only", rev).splitlines()
