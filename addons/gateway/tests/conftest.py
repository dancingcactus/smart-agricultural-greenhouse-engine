import pytest

from gateway import deps


@pytest.fixture(autouse=True)
def _isolated_gateway_env(tmp_path, monkeypatch):
    """Keep tests off the real call log, API key and forced as-of."""
    monkeypatch.setenv("GATEWAY_CALL_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.delenv("GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("GATEWAY_AS_OF", raising=False)
    yield
    from gateway.main import app
    app.dependency_overrides.clear()
    deps.get_ha.cache_clear()
    deps.get_vm.cache_clear()
