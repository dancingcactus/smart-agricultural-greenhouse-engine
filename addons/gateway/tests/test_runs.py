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


def test_daily_groups_by_the_viewers_local_day(client):
    from gateway import config, runs
    day = 86400
    now = 1_800_000_000.0  # 2027-01-15T08:00:00Z
    conn = runs.connect(config.runs_db())
    runs.add(conn, {"role": "mid", "cost_usd": 0.02}, now=now - 1 * 3600)          # same UTC day
    runs.add(conn, {"role": "mid", "cost_usd": 0.03, "prompt_tokens": 10}, now=now - 1 * day)
    runs.add(conn, {"role": "small"}, now=now - 1 * day)                              # no cost reported
    runs.add(conn, {"role": "reviewer", "cost_usd": 9.0}, now=now - 40 * day)         # outside the range
    out = runs.daily(conn, 3, 0, now=now)
    conn.close()
    assert out["days"] == ["2027-01-13", "2027-01-14", "2027-01-15"]
    by = {(r["day"], r["role"]): r for r in out["rows"]}
    assert by[("2027-01-15", "mid")]["cost_usd"] == pytest.approx(0.02)
    assert by[("2027-01-14", "small")]["unpriced"] == 1 and by[("2027-01-14", "mid")]["prompt_tokens"] == 10
    assert not any(r["role"] == "reviewer" for r in out["rows"])

    # 8:00 UTC is 21:00 the previous evening at UTC-11: the same rows land on earlier local days.
    conn = runs.connect(config.runs_db())
    west = runs.daily(conn, 2, -11 * 60, now=now)
    conn.close()
    assert west["days"] == ["2027-01-13", "2027-01-14"]
    assert {r["day"] for r in west["rows"] if r["role"] == "mid"} == {"2027-01-13", "2027-01-14"}


def test_daily_route_validates(client):
    assert len(client.get("/runs/daily?days=7").json()["days"]) == 7
    assert client.get("/runs/daily?days=0").status_code == 400
    assert client.get("/runs/daily?offset_minutes=9999").status_code == 400
