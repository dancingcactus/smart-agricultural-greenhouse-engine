import pytest
from fastapi.testclient import TestClient

from gateway import deps, review
from gateway.glossary import store as gstore
from gateway.main import app
from gateway.routes import review as review_route

NOW = 1_800_000_000.0  # 2027-01-15 08:00 UTC
H = 3600


def role(*ids):
    return [{"entity_id": i} for i in ids]


ROLES = {"greenhouse_temperature": role("sensor.t"), "greenhouse_humidity": role("sensor.h"),
         "outside_temperature": role("sensor.o"), "target_temp_low": role("sensor.low"),
         "target_temp_high": role("sensor.high"), "mode_switch": role("input_select.mode"),
         "equipment_north_fan": role("switch.fan")}


def st(value):
    return {"state": str(value), "attributes": {}}


STATES = {"sensor.t": st(70), "sensor.h": st(60), "sensor.o": st(40), "sensor.low": st(65), "sensor.high": st(78),
          "input_select.mode": st("Growing"), "switch.fan": st("off")}


def steps(*pairs):
    """History from (hours_ago, value) pairs, oldest first."""
    return [(NOW - h * H, v) for h, v in pairs]


HIST = {"sensor.t": steps((30, "70"), (12, "72"), (6, "69")), "sensor.h": steps((30, "60")),
        "sensor.o": steps((30, "40")), "switch.fan": steps((30, "off"), (10, "on"), (8, "off"), (4, "on"), (3, "off"))}
GREEN = {"status": "green", "nothing_to_report": True, "mode": "Growing", "findings": [], "roles_missing": [], "skipped": []}


def go(history=None, forecast=None, states=None, watch_result=None, **kw):
    return review.daily(NOW, ROLES, states or STATES, HIST if history is None else history, watch_result or GREEN, forecast, **kw)


def test_quiet_day_has_nothing_to_report():
    out = go()
    assert out["status"] == "green" and out["nothing_to_report"] and not out["wake_daily_early"]
    assert out["temperature"]["pct_in_band"] == 100.0 and out["temperature"]["min"] == 69.0
    assert out["equipment"]["equipment_north_fan"]["runtime_hours"] == 3.0
    assert out["equipment"]["equipment_north_fan"]["runs"] == 2
    assert out["equipment"]["equipment_north_fan"]["longest_run_minutes"] == 120
    assert "light" in out["not_measured"]


def test_time_in_band_uses_the_live_setpoints():
    cold = {**HIST, "sensor.t": steps((30, "60"))}  # below 65 all day
    out = go(history=cold)
    assert out["status"] == "amber" and out["temperature"]["hours_below"] == 24.0
    assert any(f["check"] == "time_in_band" for f in out["findings"])
    assert go(history=cold, states={**STATES, "sensor.low": st(55)})["nothing_to_report"]


def test_night_humidity_counts_only_night_hours():
    # time zone UTC: 08:00 now. Wet from 14 hours ago (18:00) to 4 hours ago (04:00): night is 20:00-04:00 = 8 hours
    wet = {**HIST, "sensor.h": steps((30, "60"), (14, "90"), (4, "60"))}
    out = go(history=wet)
    assert out["humidity"]["hours_at_or_above_risk"] == 10.0 and out["humidity"]["night_hours_at_or_above_risk"] == 8.0
    assert any(f["check"] == "night_humidity" for f in out["findings"])
    # the same wet period in a time zone five hours behind falls mostly in the evening/afternoon
    other = go(history=wet, time_zone="America/Chicago")
    assert other["humidity"]["night_hours_at_or_above_risk"] != 8.0


def test_forecast_risks_convert_units():
    points = [{"valid_time": NOW + 5 * H, "temperature": -2.0, "templow": None, "wind_speed": 10.0, "wind_gust_speed": 60.0},
              {"valid_time": NOW + 80 * H, "temperature": 40.0, "templow": None, "wind_speed": None, "wind_gust_speed": None}]
    forecast = {"issued_at": NOW - H, "units": {"temperature_unit": "°C", "wind_speed_unit": "km/h"}, "points": points}
    out = go(forecast=forecast)
    kinds = {f["check"] for f in out["findings"]}
    assert {"forecast_frost", "forecast_wind"} <= kinds and "forecast_heat" not in kinds  # 80 h out is beyond 72 h
    assert out["forecast"]["coldest"]["temperature_f"] == pytest.approx(28.4)
    assert go()["forecast"]["available"] is False


def test_watch_findings_lead_and_wake_early():
    bad = {**GREEN, "status": "red", "nothing_to_report": False,
           "findings": [{"severity": "red", "check": "hot_alarm", "entity_id": "sensor.t", "message": "hot", "evidence": {}}]}
    out = go(watch_result=bad)
    assert out["status"] == "red" and out["wake_daily_early"] and out["findings"][0]["source"] == "watch"


def test_charging_mode_skips_the_band():
    charging = {**STATES, "input_select.mode": st("Charging Earth Battery")}
    out = go(history={**HIST, "sensor.t": steps((30, "50"))}, states=charging,
             watch_result={**GREEN, "mode": "Charging Earth Battery"})
    assert "pct_in_band" not in out["temperature"] and out["nothing_to_report"]
    assert out["cadence"]["loops"] == ["daily", "weekly"]


def test_cadence_by_mode():
    assert review.loops_due("Growing")["loops"] == ["watch", "daily", "evening", "weekly"]
    assert review.loops_due("Off")["loops"] == ["weekly"]
    unknown = review.loops_due("Mystery")
    assert unknown["cadence"] == "growing" and unknown["recognised"] is False
    assert review.loops_due(None)["recognised"] is False


class FakeHA:
    mode = "Growing"

    def states(self):
        return [{"entity_id": k, **v, "state": self.mode if k == "input_select.mode" else v["state"],
                 "last_reported": "2027-01-15T07:59:00+00:00", "last_updated": "2027-01-15T07:59:00+00:00"}
                for k, v in STATES.items()]

    def get(self, path, **params):
        if path == "/config":
            return {"time_zone": "America/Chicago"}
        return [[{"entity_id": i, "state": STATES[i]["state"], "last_changed": "2027-01-14T00:00:00+00:00"}]
                for i in params["filter_entity_id"].split(",")]


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOSSARY_DB", str(tmp_path / "g.sqlite3"))
    conn = gstore.connect(str(tmp_path / "g.sqlite3"))
    for eid, name in (("sensor.t", "greenhouse_temperature"), ("input_select.mode", "mode_switch"),
                      ("switch.fan", "equipment_north_fan")):
        conn.execute("INSERT INTO entries(entity_id, active, meaning, aliases, status, referenced_by, usage) "
                     "VALUES (?, 1, 'x', ?, 'approved', '[]', '{}')", (eid, f'["role:{name}"]'))
    conn.commit()
    conn.close()
    monkeypatch.setattr(review_route.time, "time", lambda: NOW)
    fake = FakeHA()
    app.dependency_overrides[deps.get_ha] = lambda: fake
    yield TestClient(app), fake
    app.dependency_overrides.clear()


def test_routes(web):
    client, fake = web
    assert client.get("/review/cadence").json()["loops"] == ["watch", "daily", "evening", "weekly"]
    fake.mode = "Off"
    assert client.get("/review/cadence").json()["loops"] == ["weekly"]
    body = client.get("/review/daily").json()
    assert body["time_zone"] == "America/Chicago" and "equipment_north_fan" in body["equipment"]
    assert client.get("/review/daily?hours=0").status_code == 400
    assert client.get("/review/daily", headers={"X-As-Of": "1700000000"}).status_code == 409
    assert client.get("/review/cadence", headers={"X-As-Of": "1700000000"}).status_code == 409
