import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from gateway import deps
from gateway.main import app

T = 1_700_000_000.0  # 2023-11-14T22:13:20Z


class FakeHA:
    def __init__(self):
        self.gets = []
        self.ws = []

    def states(self):
        return [
            {"entity_id": "sensor.a", "state": "1", "attributes": {"friendly_name": "A"}},
            {"entity_id": "camera.door", "state": "idle",
             "attributes": {"access_token": "SECRET", "entity_picture": "/api/camera_proxy/camera.door?token=SECRET"}},
        ]

    def get(self, path, **params):
        self.gets.append((path, params))
        return [[{"entity_id": "sensor.a", "state": "1"}]] if "/history/" in path else {"path": path}

    def ws_list(self, command):
        self.ws.append(command)
        return [{"entity_id": "sensor.a"}]

    def ws_call(self, command, **params):
        self.ws.append((command, params))
        if command == "trace/list":
            return [{"run_id": "1", "timestamp": {"start": "2023-11-14T20:00:00+00:00"}},
                    {"run_id": "2", "timestamp": {"start": "2023-11-14T23:00:00+00:00"}}]
        return {"run_id": params["run_id"], "timestamp": {"start": "2023-11-14T23:00:00+00:00"}}


@pytest.fixture
def ha():
    fake = FakeHA()
    app.dependency_overrides[deps.get_ha] = lambda: fake
    return fake


@pytest.fixture
def client():
    return TestClient(app)


def test_states_redacts_and_filters(ha, client):
    body = client.get("/ha/states", params={"domain": "camera"}).json()
    assert [r["entity_id"] for r in body] == ["camera.door"]
    assert "SECRET" not in json.dumps(body)


def test_live_endpoints_refused_under_as_of(ha, client):
    for path in ("/ha/states", "/ha/states/sensor.a", "/ha/registry/entity", "/ha/services"):
        r = client.get(path, headers={"X-As-Of": str(T)})
        assert r.status_code == 409, path
    assert ha.gets == [] and ha.ws == []  # never reached Home Assistant


def test_history_end_is_clipped_to_as_of(ha, client):
    r = client.get("/ha/history", params={"entity_id": "sensor.a", "end": "2030-01-01T00:00:00Z"},
                   headers={"X-As-Of": str(T)})
    assert r.status_code == 200
    path, params = ha.gets[0]
    assert params["end_time"].startswith("2023-11-14T22:13:20")
    assert path.startswith("/history/period/2023-11-13T22:13:20")  # default window is 24h before end


def test_forced_as_of_cannot_be_loosened(ha, client, monkeypatch):
    monkeypatch.setenv("GATEWAY_AS_OF", str(T))
    client.get("/ha/history", params={"entity_id": "sensor.a"}, headers={"X-As-Of": str(T + 99999)})
    assert ha.gets[0][1]["end_time"].startswith("2023-11-14T22:13:20")


def test_history_validation(ha, client):
    assert client.get("/ha/history", params={"entity_id": "../config"}).status_code == 400
    assert client.get("/ha/history", params={"entity_id": "sensor.a", "start": "2023-12-01T00:00:00Z"},
                      headers={"X-As-Of": str(T)}).status_code == 400  # start is after as-of
    assert client.get("/ha/history", params={"entity_id": "sensor.a", "start": "now-1h"}).status_code == 400
    assert client.get("/ha/states/..%2Fconfig").status_code in (400, 404)
    assert ha.gets == []


def test_traces_filtered_by_as_of(ha, client):
    live = client.get("/ha/traces", params={"automation_id": "abc"}).json()
    assert [t["run_id"] for t in live] == ["2", "1"]
    past = client.get("/ha/traces", params={"automation_id": "abc"}, headers={"X-As-Of": str(T)}).json()
    assert [t["run_id"] for t in past] == ["1"]
    assert client.get("/ha/traces/abc/2", headers={"X-As-Of": str(T)}).status_code == 404
    assert client.get("/ha/traces/abc/2").status_code == 200


@pytest.fixture
def vm():
    with respx.mock(base_url="http://vm", assert_all_called=False) as router:
        app.dependency_overrides[deps.get_vm] = lambda: httpx.Client(base_url="http://vm")
        router.get("/api/v1/query").respond(json={"status": "success", "data": {"result": []}})
        router.get("/api/v1/query_range").respond(json={"status": "success", "data": {"result": []}})
        router.get("/api/v1/series").respond(json={"status": "success", "data": []})
        yield router


def test_vm_instant_query_time_clipped(vm, client):
    assert client.get("/vm/query", params={"query": "up", "time": str(T + 5000)},
                      headers={"X-As-Of": str(T)}).status_code == 200
    assert float(vm.calls.last.request.url.params["time"]) == T
    client.get("/vm/query", params={"query": "up"}, headers={"X-As-Of": str(T)})
    assert float(vm.calls.last.request.url.params["time"]) == T  # default "now" becomes as-of


def test_vm_range_end_clipped_and_no_extra_params(vm, client):
    r = client.get("/vm/query_range", params={"query": "up", "start": str(T - 3600), "end": str(T + 3600),
                                              "step": "60s", "latency_offset": "0s", "nocache": "1"},
                   headers={"X-As-Of": str(T)})
    assert r.status_code == 200
    sent = vm.calls.last.request.url.params
    assert float(sent["end"]) == T
    assert set(sent.keys()) == {"query", "start", "end", "step"}  # unknown params are dropped


def test_vm_rejects_future_reading_queries(vm, client):
    for q in ("up @ 1800000000", "up offset -1h"):
        assert client.get("/vm/query", params={"query": q}, headers={"X-As-Of": str(T)}).status_code == 400
    assert client.get("/vm/series", params={"match[]": "up @ end()"}).status_code == 400
    assert vm.calls.call_count == 0


def test_vm_range_start_after_as_of_rejected(vm, client):
    r = client.get("/vm/query_range", params={"query": "up", "start": str(T + 10)}, headers={"X-As-Of": str(T)})
    assert r.status_code == 400 and vm.calls.call_count == 0


def test_vm_series_clipped(vm, client):
    client.get("/vm/series", params={"match[]": ['{__name__="W_value"}', "up"], "end": str(T + 99)},
               headers={"X-As-Of": str(T)})
    sent = vm.calls.last.request.url
    assert float(sent.params["end"]) == T and sent.params.get_list("match[]") == ['{__name__="W_value"}', "up"]


def test_vm_auth_failure_is_not_leaked_as_401(vm, client):
    vm.get("/api/v1/query").respond(401)
    assert client.get("/vm/query", params={"query": "up"}).status_code == 502
