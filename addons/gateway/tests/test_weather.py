import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from gateway import config
from gateway.ha_client import HAClient
from gateway.jobs import weather_archive
from gateway.jobs.scheduler import start_scheduler
from gateway.main import app
from gateway.weather import archive

T0 = 1_800_000_000.0  # pull times below are T0 + n hours
H = 3600


def iso(ts):
    from gateway.asof import iso as _iso
    return _iso(ts)


class FakeHA:
    def __init__(self, features=3, fail=()):
        self.features, self.fail, self.calls = features, set(fail), []
        self.temp_offset = 0  # lets a test change the forecast between pulls

    def states(self):
        return [{"entity_id": "weather.home", "state": "sunny",
                 "attributes": {"temperature": 71.0, "humidity": 40, "temperature_unit": "°F",
                                "wind_speed_unit": "mph", "supported_features": self.features}}]

    def ws_first_event(self, command, timeout=20.0, **params):
        self.calls.append((command, params))
        ftype = params["forecast_type"]
        if ftype in self.fail:
            raise RuntimeError("provider failed")
        base = T0
        if ftype == "hourly":
            fc = [{"datetime": iso(base + i * H), "temperature": 70 + i + self.temp_offset,
                   "condition": "sunny", "precipitation_probability": 10, "mystery": i} for i in range(100)]
        else:
            fc = [{"datetime": iso(base + i * 86400), "temperature": 80, "templow": 60, "condition": "cloudy"}
                  for i in range(5)]
        return {"type": ftype, "forecast": fc}


@pytest.fixture
def conn(tmp_path):
    c = archive.connect(str(tmp_path / "w.sqlite3"))
    yield c
    c.close()


def test_supported_types_from_feature_bits():
    assert archive.supported_types({"supported_features": 1}) == ["daily"]
    assert archive.supported_types({"supported_features": 7}) == ["daily", "hourly", "twice_daily"]
    assert archive.supported_types({}) == ["daily", "hourly"]


def test_pull_stores_forecasts_observation_and_units(conn):
    out = archive.pull(FakeHA(), conn, "weather.home", now=T0, horizon_hours=72)
    assert out["stored"]["hourly"] == {"points": 73, "unchanged": False}  # 0..72h inclusive, rest trimmed
    assert out["stored"]["daily"]["points"] == 5 and not out["errors"]
    obs = archive.observations(conn, "weather.home", T0 - 1, T0 + 1)
    assert obs[0]["condition"] == "sunny" and obs[0]["attributes"]["temperature"] == 71.0
    got = archive.forecast_as_of(conn, "weather.home", "hourly", T0 + 60)
    assert got["units"] == {"temperature_unit": "°F", "wind_speed_unit": "mph"}
    assert got["points"][3]["temperature"] == 73 and got["points"][3]["extra"] == {"mystery": 3}


def test_unchanged_forecast_is_stored_once_but_readable_at_every_pull(conn):
    ha = FakeHA()
    archive.pull(ha, conn, "weather.home", now=T0)
    second = archive.pull(ha, conn, "weather.home", now=T0 + H)
    assert second["stored"]["hourly"]["unchanged"] is True
    assert conn.execute("SELECT COUNT(DISTINCT pull_id) FROM forecasts").fetchone()[0] == 2  # hourly + daily only once
    at_second = archive.forecast_as_of(conn, "weather.home", "hourly", T0 + H + 5)
    assert at_second["issued_at"] == T0 + H and len(at_second["points"]) == 73  # pointer resolved


def test_as_of_never_returns_a_later_forecast(conn):
    ha = FakeHA()
    archive.pull(ha, conn, "weather.home", now=T0)
    ha.temp_offset = 5  # the forecast changes for the second pull
    archive.pull(ha, conn, "weather.home", now=T0 + 6 * H)
    assert archive.forecast_as_of(conn, "weather.home", "hourly", T0 - 1) is None  # nothing existed yet
    first = archive.forecast_as_of(conn, "weather.home", "hourly", T0 + 5 * H)
    assert first["issued_at"] == T0 and first["points"][0]["temperature"] == 70
    later = archive.forecast_as_of(conn, "weather.home", "hourly", T0 + 6 * H)
    assert later["issued_at"] == T0 + 6 * H and later["points"][0]["temperature"] == 75
    assert archive.forecast_as_of(conn, "weather.home", "hourly", T0 + 5 * H, horizon_hours=2)["points"][-1]["valid_time"] \
        == T0 + 2 * H


def test_observations_are_clipped(conn):
    ha = FakeHA()
    for i in range(4):
        archive.pull(ha, conn, "weather.home", now=T0 + i * H)
    assert len(archive.observations(conn, "weather.home", T0, T0 + 2 * H)) == 3
    assert len(archive.observations(conn, "weather.home", T0, T0 + 10 * H, limit=2)) == 2


def test_partial_failure_is_reported_and_total_failure_raises(conn):
    out = archive.pull(FakeHA(fail=["hourly"]), conn, "weather.home", now=T0)
    assert "hourly" in out["errors"] and "daily" in out["stored"]
    with pytest.raises(RuntimeError, match="no forecast could be read"):
        archive.pull(FakeHA(fail=["hourly", "daily"]), conn, "weather.home", now=T0 + H)
    with pytest.raises(RuntimeError, match="does not exist"):
        archive.pull(FakeHA(), conn, "weather.nope", now=T0)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_DB", str(tmp_path / "w.sqlite3"))
    monkeypatch.setenv("WEATHER_ENTITIES", "weather.home")
    conn = archive.connect(str(tmp_path / "w.sqlite3"))
    ha = FakeHA()
    archive.pull(ha, conn, "weather.home", now=T0)
    ha.temp_offset = 5
    archive.pull(ha, conn, "weather.home", now=T0 + 6 * H)
    conn.close()
    return TestClient(app)


def test_forecast_route_honours_x_as_of(api):
    hdr = lambda ts: {"X-As-Of": str(ts)}
    assert api.get("/weather/forecast", headers=hdr(T0 - 5)).status_code == 404
    early = api.get("/weather/forecast", headers=hdr(T0 + 3 * H)).json()
    assert early["issued_at"] == T0 and early["points"][0]["temperature"] == 70
    late = api.get("/weather/forecast", params={"type": "daily"}, headers=hdr(T0 + 7 * H)).json()
    assert late["issued_at"] == T0 + 6 * H
    assert api.get("/weather/forecast", params={"type": "weekly"}).status_code == 400
    assert api.get("/weather/forecast", params={"entity_id": "sensor.x"}).status_code == 400


def test_forced_as_of_applies_to_weather_too(api, monkeypatch):
    monkeypatch.setenv("GATEWAY_AS_OF", str(T0 + 3 * H))
    got = api.get("/weather/forecast", headers={"X-As-Of": str(T0 + 99 * H)}).json()
    assert got["issued_at"] == T0  # the caller cannot read past the forced time


def test_observations_and_status_routes(api):
    rows = api.get("/weather/observations", params={"start": str(T0 - 1), "end": str(T0 + 100 * H)},
                   headers={"X-As-Of": str(T0 + 3 * H)}).json()
    assert [r["observed_at"] for r in rows] == [T0]  # the pull at +6h is in the future of the as-of time
    status = api.get("/weather/status").json()
    assert status["configured"] == ["weather.home"]
    assert {f["forecast_type"] for f in status["forecasts"]} == {"hourly", "daily"}


class FakeWS:
    def __init__(self, messages):
        self.messages, self.sent = list(messages), []

    def recv(self, timeout=None):
        return json.dumps(self.messages.pop(0))

    def send(self, data):
        self.sent.append(json.loads(data))


def _client_with(monkeypatch, messages):
    ws = FakeWS([{"type": "auth_required"}, {"type": "auth_ok"}, *messages])

    @contextmanager
    def fake_connect(url):
        fake_connect.url = url
        yield ws

    monkeypatch.setattr("gateway.ha_client.connect", fake_connect)
    return HAClient("http://supervisor/core", "tok"), ws, fake_connect


def test_ws_first_event_reads_result_then_event(monkeypatch):
    client, ws, conn = _client_with(monkeypatch, [
        {"id": 9, "type": "event", "event": {"ignored": True}},   # another subscription's traffic
        {"id": 1, "type": "result", "success": True, "result": None},
        {"id": 1, "type": "event", "event": {"type": "hourly", "forecast": [{"datetime": "x"}]}},
    ])
    event = client.ws_first_event("weather/subscribe_forecast", entity_id="weather.home", forecast_type="hourly")
    assert event["forecast"] == [{"datetime": "x"}] and conn.url == "ws://supervisor/core/websocket"
    assert ws.sent[1] == {"id": 1, "type": "weather/subscribe_forecast", "entity_id": "weather.home",
                          "forecast_type": "hourly"}


def test_ws_first_event_error_and_allowlist(monkeypatch):
    client, _, _ = _client_with(monkeypatch, [{"id": 1, "type": "result", "success": False, "error": {"code": "x"}}])
    with pytest.raises(RuntimeError, match="failed"):
        client.ws_first_event("weather/subscribe_forecast", entity_id="weather.home", forecast_type="daily")
    for command, params in [("subscribe_events", {"entity_id": "weather.home", "forecast_type": "daily"}),
                            ("weather/subscribe_forecast", {"entity_id": "light.x", "forecast_type": "daily"}),
                            ("weather/subscribe_forecast", {"entity_id": "weather.home", "forecast_type": "nope"})]:
        with pytest.raises(PermissionError):
            client.ws_first_event(command, **params)


def test_weather_job_uses_the_configured_entities(tmp_path, monkeypatch):
    monkeypatch.setenv("WEATHER_DB", str(tmp_path / "w.sqlite3"))
    monkeypatch.setenv("WEATHER_ENTITIES", "weather.home\nweather.missing")
    monkeypatch.setattr("gateway.deps.get_ha", lambda: FakeHA())
    out = weather_archive.run_from_env()
    assert len(out["pulled"]) == 1 and "weather.missing" in out["failed"]
    monkeypatch.setenv("WEATHER_ENTITIES", "weather.missing")
    with pytest.raises(RuntimeError, match="weather pull failed"):
        weather_archive.run_from_env()
    assert config.weather_horizon_hours() == 72


def test_scheduler_registers_extra_jobs():
    sched = start_scheduler("15 3 * * *", dict, extra_jobs={"weather_archive": ("10 * * * *", dict)})
    try:
        assert "weather_archive" in {j.id for j in sched.get_jobs()}  # the startup copy may already have run
    finally:
        sched.shutdown(wait=False)
