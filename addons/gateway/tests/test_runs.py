import time

import pytest
from fastapi.testclient import TestClient

from gateway.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DB", str(tmp_path / "runs.sqlite3"))
    return TestClient(app)


def post(client, **kw):
    return client.post("/runs", json={"role": "mid", "model": "qwen/qwen3.8-27b", **kw})


def test_add_and_list(client):
    assert post(client, prompt_hash="abc", cost_usd=0.01, prompt_tokens=100).status_code == 200
    rows = client.get("/runs").json()
    assert rows[0]["prompt_hash"] == "abc" and rows[0]["role"] == "mid"


def test_spend_per_role_and_window(client, tmp_path):
    from gateway import config, runs
    conn = runs.connect(config.runs_db())
    runs.add(conn, {"role": "reviewer", "cost_usd": 1.5}, now=time.time() - 3 * 86400)  # outside a day
    conn.close()
    post(client, cost_usd=0.02)
    post(client, cost_usd=0.03)
    post(client)  # cost unknown
    client.post("/runs", json={"role": "reviewer", "cost_usd": 0.5})
    spend = client.get("/runs/spend").json()["roles"]
    assert spend["mid"]["calls"] == 3 and spend["mid"]["unpriced"] == 1
    assert spend["mid"]["cost_usd"] == pytest.approx(0.05)
    assert spend["reviewer"]["calls"] == 1  # the 3-day-old row is excluded
    assert client.get("/runs/spend?hours=100").json()["roles"]["reviewer"]["calls"] == 2


def test_rejects_bad_rows_and_other_writes(client):
    assert client.post("/runs", json={"cost_usd": 1}).status_code == 422  # role required
    assert client.post("/runs", json={"role": "x", "cost_usd": -1}).status_code == 422
    assert client.delete("/runs").status_code == 405  # still read-only otherwise


def test_requires_key_when_configured(client, monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEY", "k")
    assert post(client).status_code == 401
    assert client.post("/runs", json={"role": "mid"}, headers={"X-Gateway-Key": "k"}).status_code == 200
