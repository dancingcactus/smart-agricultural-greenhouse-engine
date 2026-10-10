import pytest
from fastapi.testclient import TestClient

from gateway import deps
from gateway.calllog import CallLog
from gateway.main import app
from tests.test_read_api import FakeHA


@pytest.fixture
def client(tmp_path):
    app.dependency_overrides[deps.get_ha] = lambda: FakeHA()
    return TestClient(app)


def _log(tmp_path):
    return CallLog(tmp_path / "calls.jsonl").read()


def test_writes_are_refused_and_logged(client, tmp_path):
    for method in ("post", "put", "patch", "delete"):
        r = client.request(method.upper(), "/ha/services/light/turn_on", json={"entity_id": "light.x"})
        assert r.status_code == 405
    rows = _log(tmp_path)
    assert len(rows) == 4 and all(r["refused"] == "the gateway is read-only" for r in rows)


def test_no_route_can_call_a_service(client):
    paths = app.openapi()["paths"]  # the schema lists every real route and method
    assert "/ha/states" in paths and "/vm/query" in paths  # guard against an empty listing
    assert not any("service" in p and p != "/ha/services" for p in paths)
    allowed = {(p, m) for p, ops in paths.items() for m in ops}
    from gateway.middleware import ALLOWED_POSTS
    writes = {(p, m) for p, m in allowed if m != "get"}
    # Nothing else can write, and none are missing. Each only changes this add-on's own data.
    assert writes == {(p, "post") for p in ALLOWED_POSTS} | {("/glossary/{entity_id}", "put")}
    assert ALLOWED_POSTS == {"/catalog/run", "/snapshot/run", "/glossary/drafts"}


def test_api_key_enforced_and_health_open(client, monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEY", "s3cret")
    assert client.get("/health").status_code == 200
    assert client.get("/ha/states").status_code == 401
    assert client.get("/ha/states", headers={"X-Gateway-Key": "wrong"}).status_code == 401
    assert client.get("/ha/states", headers={"X-Gateway-Key": "s3cret"}).status_code == 200


def test_every_call_is_logged_with_route_as_of_and_caller(client, tmp_path):
    client.get("/ha/states/sensor.a", headers={"X-As-Of": "123", "X-Caller": "watch"})
    client.get("/nope")
    first, second = _log(tmp_path)
    assert (first["route"], first["as_of"], first["caller"], first["status"]) == ("/ha/states/{entity_id}", "123", "watch", 409)
    assert second["status"] == 404


def test_log_never_contains_tokens(client, tmp_path):
    client.get("/ha/history", params={"entity_id": "sensor.a", "api_key": "hunter2", "token": "t0ken"})
    text = (tmp_path / "calls.jsonl").read_text()
    assert "hunter2" not in text and "t0ken" not in text


def test_call_summary(client):
    client.get("/ha/states")
    client.get("/ha/states")
    client.post("/ha/anything")
    s = client.get("/calls/summary").json()
    assert s["by_route"]["GET /ha/states"] == 2
    assert s["refused"][0]["status"] == 405


def test_call_log_rotates(tmp_path):
    log = CallLog(tmp_path / "c.jsonl", max_bytes=50, backups=2)
    for i in range(10):
        log.write({"i": i, "pad": "x" * 30})
    assert (tmp_path / "c.jsonl.1").exists() and (tmp_path / "c.jsonl.2").exists()
    assert not (tmp_path / "c.jsonl.3").exists()
