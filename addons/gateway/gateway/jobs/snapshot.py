"""Scheduled config snapshot (thin wrapper so the scheduler imports a stable name)."""

from gateway.snapshot.run import run_from_env

__all__ = ["run_from_env"]
