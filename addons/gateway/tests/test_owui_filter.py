"""The Open WebUI run-log filter: role detection, cap decisions and the row it posts."""

import asyncio
import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

FILTER = Path(__file__).resolve().parents[3] / "owui" / "functions" / "sage_run_log.py"
spec = importlib.util.spec_from_file_location("sage_run_log", FILTER)
mod = importlib.util.module_from_spec(spec)
sys.modules["sage_run_log"] = mod
spec.loader.exec_module(mod)


def test_role_of_only_matches_sage_presets():
    assert mod.role_of("sage-mid") == "mid"
    assert mod.role_of("qwen/qwen3.8-27b") is None and mod.role_of(None) is None


def test_prompt_hash_matches_the_preset_builder():
    root = FILTER.parents[2]
    spec = importlib.util.spec_from_file_location("build_presets", root / "scripts" / "build_presets.py")
    bp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bp)
    preset = bp.build()[0]
    assert preset["meta"]["description"].endswith(mod.prompt_hash(preset["params"]["system"]))


def test_over_cap():
    spend = {"reviewer": {"calls": 10, "cost_usd": 0.4}, "mid": {"calls": 3, "cost_usd": 1.2}}
    caps, calls = {"reviewer": 1.0, "mid": 1.0}, {"reviewer": 10}
    assert "daily calls" in mod.over_cap("reviewer", spend, caps, calls)
    assert "spent" in mod.over_cap("mid", spend, caps, calls)
    assert mod.over_cap("small", spend, caps, calls) is None  # no spend, no cap


def test_run_row_collects_the_stamp():
    model = {"info": {"base_model_id": "z-ai/glm-5.3", "params": {"system": "hello"},
                      "meta": {"toolIds": ["sage_gateway"]}}}
    msg = {"usage": {"input_tokens": 100, "output_tokens": 20, "cost": 0.003}}
    row = mod.run_row("builder", model, msg, "c1", "m1", "u1", "abc123")
    assert row["model"] == "z-ai/glm-5.3" and row["tool_set"] == "abc123:sage_gateway"
    assert row["prompt_hash"] == hashlib.sha256(b"hello").hexdigest()[:10]
    assert (row["prompt_tokens"], row["completion_tokens"], row["cost_usd"]) == (100, 20, 0.003)


def test_inlet_blocks_over_cap_and_ignores_other_models(monkeypatch):
    f = mod.Filter()
    other = {"model": "something-else"}
    assert asyncio.run(f.inlet(other)) is other  # untouched, no network

    class Resp:
        def raise_for_status(self): ...
        def json(self): return {"roles": {"reviewer": {"calls": 99, "cost_usd": 0}}}

    class Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): ...
        async def get(self, *a, **k): return Resp()

    monkeypatch.setattr(mod.httpx, "AsyncClient", Client)
    with pytest.raises(mod.SageBlocked, match="daily cap"):
        asyncio.run(f.inlet({"model": "sage-reviewer"}))
    assert asyncio.run(f.inlet({"model": "sage-mid"}))["usage"] == {"include": True}


def test_gateway_down_fails_open_unless_configured():
    f = mod.Filter()
    f.valves.gateway_url = "http://127.0.0.1:1"  # nothing listens
    assert asyncio.run(f.inlet({"model": "sage-mid"}))["model"] == "sage-mid"
    f.valves.fail_closed = True
    with pytest.raises(mod.SageBlocked, match="cannot check"):
        asyncio.run(f.inlet({"model": "sage-mid"}))
