import pytest
from fastapi.testclient import TestClient

from gateway import deps, watch
from gateway.glossary import store as gstore
from gateway.main import app
from gateway.routes import watch as watch_route

NOW = 1_800_000_000.0
H = 3600


def iso(ts):
    from gateway.asof import iso as _iso
    return _iso(ts)


def role(*ids):
    return [{"entity_id": i} for i in ids]


ROLES = {
    "greenhouse_temperature": role("sensor.gh_t"), "greenhouse_humidity": role("sensor.gh_h"),
    "outside_temperature": role("sensor.out_t"), "target_temp_low": role("sensor.low"),
    "target_temp_high": role("sensor.high"), "mode_switch": role("input_select.mode"),
    "alarm_cold": role("input_number.cold"), "alarm_hot": role("input_number.hot"),
    "target_humidity": role("input_number.rh"), "thermostat_reason": role("sensor.reason"),
}


def st(value, age_min=1, **attrs):
    return {"state": str(value), "attributes": attrs, "last_reported": iso(NOW - age_min * 60),
            "last_updated": iso(NOW - age_min * 60)}


def healthy_states(**over):
    s = {"sensor.gh_t": st(70), "sensor.gh_h": st(60), "sensor.out_t": st(40), "sensor.low": st(65),
         "sensor.high": st(78), "input_select.mode": st("Growing"), "input_number.cold": st(35),
         "input_number.hot": st(95), "input_number.rh": st(70), "sensor.reason": st("target_temp_reached")}
    s.update(over)
    return s


def moving(*values, every=1800):
    """History that changes: oldest first, ending now."""
    n = len(values)
    return [(NOW - (n - 1 - i) * every, v) for i, v in enumerate(values)]


HIST = {"sensor.gh_t": moving("69", "70", "71", "70", "70", "69", "70"), "sensor.gh_h": moving("58", "60", "61", "60", "59", "60", "60"),
        "sensor.out_t": moving("38", "39", "40", "41", "40", "40", "40")}


def go(states=None, history=None, roles=None, **kw):
    return watch.run(NOW, roles or ROLES, states or healthy_states(), history if history is not None else HIST, **kw)


def checks(out):
    return {(f["check"], f["entity_id"]) for f in out["findings"]}


def test_healthy_greenhouse_has_nothing_to_report():
    out = go()
    assert out["status"] == "green" and out["nothing_to_report"] and out["findings"] == []
    assert out["readings"]["greenhouse_temperature"]["value"] == 70.0
    assert out["roles_missing"] == []


def test_stale_unavailable_and_missing_readings():
    out = go(states=healthy_states(**{"sensor.out_t": st(40, age_min=95), "sensor.gh_h": st("unavailable")}))
    assert ("stale", "sensor.out_t") in checks(out) and ("unavailable", "sensor.gh_h") in checks(out)
    states = healthy_states()
    del states["sensor.out_t"]
    assert ("missing", "sensor.out_t") in checks(go(states=states))


def test_flat_needs_enough_history_and_no_changes():
    flat = {**HIST, "sensor.out_t": [(NOW - 5 * H, "40.0")]}  # one state, in effect for 5 hours
    assert ("flat", "sensor.out_t") in checks(go(history=flat))
    short = {**HIST, "sensor.out_t": [(NOW - 10 * 60, "40.0")]}  # only 10 minutes of history: say nothing
    assert ("flat", "sensor.out_t") not in checks(go(history=short))
    assert ("flat", "sensor.gh_t") not in checks(go())  # it moves


def test_low_battery_found_through_the_same_device():
    states = healthy_states(**{"sensor.gh_t_battery": st(12)})
    out = go(states=states, siblings={"sensor.gh_t": ["sensor.gh_t_battery", "sensor.gh_t_signal"]})
    assert ("battery", "sensor.gh_t_battery") in checks(out)
    ok = go(states=healthy_states(**{"sensor.gh_t_battery": st(80)}), siblings={"sensor.gh_t": ["sensor.gh_t_battery"]})
    assert ok["nothing_to_report"]


def test_temperature_band_uses_the_live_setpoints_and_requires_persistence():
    cold_hist = {**HIST, "sensor.gh_t": moving("64", "63", "63", "62", "62", "62", "62", every=600)}
    out = go(states=healthy_states(**{"sensor.gh_t": st(62)}), history=cold_hist)
    assert ("below_band", "sensor.gh_t") in checks(out) and out["status"] == "amber"
    blip = {**HIST, "sensor.gh_t": moving("70", "70", "70", "70", "64", "70", "62", every=600)}  # recovered recently
    assert ("below_band", "sensor.gh_t") not in checks(go(states=healthy_states(**{"sensor.gh_t": st(62)}), history=blip))
    hot_hist = {**HIST, "sensor.gh_t": moving("80", "81", "82", "83", "83", "83", "83", every=600)}
    assert ("above_band", "sensor.gh_t") in checks(go(states=healthy_states(**{"sensor.gh_t": st(83)}), history=hot_hist))
    # Moving the setpoint moves the verdict: nothing is hard-coded.
    assert not go(states=healthy_states(**{"sensor.low": st(60), "sensor.gh_t": st(62)}), history=cold_hist)["findings"]


def test_alarm_limits_are_red():
    out = go(states=healthy_states(**{"sensor.gh_t": st(34)}), history={**HIST, "sensor.gh_t": moving("40", "36", "34")})
    assert out["status"] == "red" and ("cold_alarm", "sensor.gh_t") in checks(out)
    out = go(states=healthy_states(**{"sensor.gh_t": st(96)}), history={**HIST, "sensor.gh_t": moving("90", "94", "96")})
    assert ("hot_alarm", "sensor.gh_t") in checks(out)


def test_charging_mode_skips_the_growing_band():
    out = go(states=healthy_states(**{"input_select.mode": st("Charging Earth Battery"), "sensor.gh_t": st(55)}),
             history={**HIST, "sensor.gh_t": moving("55", "55", "55", "55", "55", "55", "55", every=600)})
    assert not any(f["check"] == "below_band" for f in out["findings"])
    assert any("mode is" in s for s in out["skipped"])


def test_thermostat_fault_reason():
    out = go(states=healthy_states(**{"sensor.reason": st("malfunction")}))
    assert out["status"] == "red" and ("thermostat_fault", "sensor.reason") in checks(out)
    assert go(states=healthy_states(**{"sensor.reason": st("temperature_sensor_stalled")}))["status"] == "amber"


def test_missing_roles_are_reported_as_gaps_not_crashes():
    out = go(roles={"greenhouse_temperature": role("sensor.gh_t")})
    assert "target_temp_low" in out["roles_missing"]
    assert any("temperature band" in s for s in out["skipped"])


# ---------------- the route ----------------

class FakeHA:
    def states(self):
        return [{"entity_id": k, **v} for k, v in healthy_states().items()]

    def get(self, path, **params):
        assert path.startswith("/history/period/")
        ids = params["filter_entity_id"].split(",")
        return [[{"entity_id": i, "state": "70", "last_changed": iso(NOW - 6 * H)},
                 {"state": "71", "last_changed": iso(NOW - 2 * H)}] for i in ids]


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOSSARY_DB", str(tmp_path / "g.sqlite3"))
    conn = gstore.connect(str(tmp_path / "g.sqlite3"))
    # a minimal approved glossary: one entry carrying a role
    conn.execute("INSERT INTO entries(entity_id, active, meaning, aliases, status, referenced_by, usage) "
                 "VALUES ('sensor.gh_t', 1, 'GH temp', '[\"role:greenhouse_temperature\"]', 'approved', '[]', '{}')")
    conn.commit()
    conn.close()
    monkeypatch.setattr(watch_route.time, "time", lambda: NOW)
    app.dependency_overrides[deps.get_ha] = lambda: FakeHA()
    return TestClient(app)


def test_route_runs_the_checks_and_refuses_as_of(web):
    r = web.get("/watch")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("green", "amber", "red") and "target_temp_low" in body["roles_missing"]
    assert body["readings"]["greenhouse_temperature"]["value"] == 70.0
    assert web.get("/watch", headers={"X-As-Of": "1700000000"}).status_code == 409
    assert web.get("/watch?stale_minutes=0").status_code == 400
