"""The chat tool's charts: data preparation, the spec, the page, and show_chart end to end with a fake gateway."""

import importlib.util
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

TOOL = Path(__file__).resolve().parents[3] / "owui" / "tools" / "greenhouse_gateway.py"
spec = importlib.util.spec_from_file_location("gh_tool_charts", TOOL)
m = importlib.util.module_from_spec(spec)
sys.modules["gh_tool_charts"] = m
spec.loader.exec_module(m)

T0 = 1_800_000_000.0
H = 3600


def iso(ts):
    return m._iso(ts)


def rows(entity, *pairs):
    """Minimal-response history: only the first row names the entity."""
    out = [{"entity_id": entity, "state": pairs[0][1], "last_changed": iso(pairs[0][0])}]
    out += [{"state": v, "last_changed": iso(t)} for t, v in pairs[1:]]
    return [out]


def test_history_points_keeps_numbers_and_extends_to_the_edge():
    pts = m.history_points(rows("sensor.a", (T0, "70.5"), (T0 + H, "unavailable"), (T0 + 2 * H, "71")), T0 + 3 * H)
    assert pts == [[int(T0 * 1000), 70.5], [int((T0 + 2 * H) * 1000), 71.0], [int((T0 + 3 * H) * 1000), 71.0]]
    assert m.history_points([], T0) == [] and m.history_points(rows("sensor.a", (T0, "off")), T0 + H) == []


def test_downsample_bounds_the_size_and_keeps_the_shape():
    pts = [[int(T0 * 1000) + i * 60_000, float(i)] for i in range(2000)]
    small = m.downsample(pts, 400)
    assert len(small) <= 401 and small[0][1] < small[-1][1]
    assert m.downsample(pts[:10], 400) == pts[:10]


def test_band_stats_is_time_weighted_and_reads_the_band_as_it_was():
    t = lambda h: int((T0 + h * H) * 1000)
    temp = [[t(0), 70.0], [t(1), 60.0], [t(3), 80.0], [t(4), 80.0]]       # inside 1h, below 2h, above 1h
    low, high = [[t(0), 65.0]], [[t(0), 75.0]]
    assert m.band_stats(temp, low, high) == {"below": 50.0, "inside": 25.0, "above": 25.0}
    moved = [[t(0), 65.0], [t(2), 55.0]]                                   # the low setpoint drops to 55 at hour 2
    assert m.band_stats(temp, moved, high)["below"] == 25.0
    assert m.band_stats(temp, [], high) is None


def test_colours_follow_the_kind_of_sensor_not_the_order_asked():
    a = m.assign_slots(["outside_temperature", "greenhouse_temperature"])
    assert a == {"outside_temperature": 2, "greenhouse_temperature": 1}
    c = m.assign_slots(["mystery", "greenhouse_humidity"])
    assert c["greenhouse_humidity"] == 1 and c["mystery"] == 2
    # A second sensor of a kind already drawn takes the next free colour, and none is skipped.
    d = m.assign_slots(["greenhouse_temperature", "outside_temperature", "greenhouse_humidity"])
    assert d == {"greenhouse_temperature": 1, "outside_temperature": 2, "greenhouse_humidity": 3}


def test_units_get_separate_panels_and_the_band_goes_on_the_greenhouse_temperature():
    pts = [[int(T0 * 1000), 70.0], [int((T0 + H) * 1000), 71.0]]
    band = {"°F": {"low": [[int(T0 * 1000), 65.0]], "high": [[int(T0 * 1000), 78.0]]}}
    out = m.build_chart_spec("t", T0, T0 + H, [
        {"role": "greenhouse_temperature", "entity_id": "sensor.a", "unit": "°F", "points": pts},
        {"role": "outside_temperature", "entity_id": "sensor.b", "unit": "°F", "points": pts},
        {"role": "greenhouse_humidity", "entity_id": "sensor.c", "unit": "%", "points": pts}], band)
    assert [p["unit"] for p in out["panels"]] == ["°F", "%"]
    assert out["panels"][0]["band"]["stats"]["inside"] == 100.0 and out["panels"][1]["band"] is None
    assert len(out["panels"][0]["series"]) == 2


def test_page_treats_the_spec_as_data_never_markup():
    evil = {"title": "</script><img src=x onerror=alert(1)>", "start": 0, "end": 1, "panels": []}
    page = m.chart_html(evil)
    assert "</script><img" not in page and "\\u003c/script>" in page
    body = re.search(r'<script id="spec" type="application/json">(.*?)</script>', page, re.DOTALL).group(1)
    assert json.loads(body)["title"] == evil["title"]
    assert "innerHTML" not in page and "http://www.w3.org/2000/svg" in page and "https://" not in page  # no network, no markup sink


# ---------------- show_chart end to end ----------------

ROLES = {"greenhouse_temperature": [{"entity_id": "sensor.gh_t"}], "outside_temperature": [{"entity_id": "sensor.out_t"}],
         "greenhouse_humidity": [{"entity_id": "sensor.gh_h"}], "target_temp_low": [{"entity_id": "sensor.low"}],
         "target_temp_high": [{"entity_id": "sensor.high"}]}


def fake_gateway(history=None):
    history = history if history is not None else {
        "sensor.gh_t": [(0, "70"), (1, "62"), (3, "72")], "sensor.out_t": [(0, "40"), (2, "42")],
        "sensor.gh_h": [(0, "60"), (2, "75")], "sensor.low": [(0, "65")], "sensor.high": [(0, "78")]}
    calls = []

    def transport(method, url, headers, body):
        u = urlparse(url)
        q = parse_qs(u.query)
        calls.append(u.path)
        if u.path == "/glossary/roles":
            return 200, json.dumps(ROLES).encode()
        if u.path == "/ha/states":
            units = {"sensor.gh_t": "°F", "sensor.out_t": "°F", "sensor.gh_h": "%"}
            return 200, json.dumps([{"entity_id": e, "state": "1", "attributes": {"unit_of_measurement": u_}}
                                    for e, u_ in units.items()]).encode()
        if u.path == "/ha/history":
            eid = q["entity_id"][0]
            return 200, json.dumps(rows(eid, *[(T0 - 24 * H + h * H, v) for h, v in history[eid]]) if eid in history else []).encode()
        return 404, b'{"detail":"unexpected"}'

    return transport, calls


@pytest.fixture
def tool():
    t = m.Tools()
    t.valves.GATEWAY_URL, t.valves.AS_OF = "http://gw:8099", m._iso(T0)
    transport, calls = fake_gateway()
    t._gw = lambda: m.Gateway("http://gw:8099", "k", as_of=t.valves.AS_OF, caller="open-webui", transport=transport)
    t.calls = calls
    return t


def test_show_chart_returns_an_inline_embed_and_the_numbers(tool):
    reply = tool.show_chart("greenhouse_temperature, outside_temperature, greenhouse_humidity", hours=24, title="Test")
    response, facts = reply
    assert response.headers["Content-Disposition"] == "inline" and b"Test" in response.body
    assert set(facts["series"]) == {"Greenhouse temperature", "Outside temperature", "Greenhouse humidity"}
    gh = facts["series"]["Greenhouse temperature"]
    assert (gh["min"], gh["max"], gh["latest"], gh["unit"]) == (62.0, 72.0, 72.0, "°F")
    band = facts["target_band_time_pct"]["°F"]
    assert band["below"] > 0 and band["inside"] > 0 and round(sum(band.values()), 1) == 100.0
    assert "already shown" in facts["note"]


def test_show_chart_refuses_instead_of_guessing(tool):
    assert "Unknown role" in tool.show_chart("nope") and "greenhouse_temperature" in tool.show_chart("nope")
    assert "1 to 5" in tool.show_chart("") and "1 to 5" in tool.show_chart(",".join(["greenhouse_temperature"] * 6))
    tool._gw = lambda: m.Gateway("http://gw:8099", "k", transport=fake_gateway({})[0])
    assert tool.show_chart("greenhouse_temperature").startswith("No numeric readings")


def test_show_chart_is_described_for_the_model():
    doc = m.Tools.show_chart.__doc__
    assert ":return:" in doc and all(f":param {p}:" in doc for p in ("roles", "hours", "title"))


# ---------------- equipment timelines ----------------

def seg_rows(entity, *pairs):
    return rows(entity, *pairs)


def ms(hours):
    return int((T0 + hours * H) * 1000)


def test_state_segments_are_clipped_to_the_window_and_joined():
    r = seg_rows("switch.fan", (T0 - 2 * H, "off"), (T0 + 1 * H, "ON"), (T0 + 2 * H, "on"), (T0 + 3 * H, "off"))
    segs = m.state_segments(r, ms(0), ms(5))
    assert segs == [[ms(0), ms(1), "off"], [ms(1), ms(3), "on"], [ms(3), ms(5), "off"]]  # the two "on" rows merge
    assert m.state_segments([], ms(0), ms(5)) == [] and m.state_segments(seg_rows("a.b", (T0 + 9 * H, "on")), ms(0), ms(5)) == []


def test_run_stats_counts_starts_short_runs_and_ignores_cut_off_runs():
    # on at the very start (cut off), then 3-minute run, then 40-minute run, then on again at the end (cut off)
    t = lambda minutes: int((T0 + minutes * 60) * 1000)
    segs = [[t(0), t(10), "on"], [t(10), t(30), "off"], [t(30), t(33), "on"], [t(33), t(60), "off"],
            [t(60), t(100), "on"], [t(100), t(150), "unavailable"], [t(150), t(180), "on"]]
    st = m.run_stats(segs, t(0), t(180))
    assert st["starts"] == 3 and st["completed_runs"] == 2          # the first run began before the window
    assert st["active_minutes"] == 10 + 3 + 40 + 30 and st["short_runs"] == 1
    assert st["shortest_run_minutes"] == 3.0 and st["average_run_minutes"] == 21.5 and st["longest_run_minutes"] == 40.0
    assert st["offline_minutes"] == 50.0 and st["now"] == "on" and st["since"] == t(150)
    assert m.run_stats([], 0, 1000)["now"] is None


def test_timeline_spec_has_lanes_in_order_and_a_temperature_band():
    segs = [[ms(0), ms(2), "on"], [ms(2), ms(4), "off"]]
    temp = {"points": [[ms(0), 70.0], [ms(4), 60.0]], "unit": "°F", "band": {"low": [[ms(0), 65.0]], "high": [[ms(0), 78.0]]}}
    spec = m.build_timeline_spec("t", T0, T0 + 4 * H, [{"role": "equipment_north_fan", "entity_id": "switch.n", "segments": segs}], temp)
    assert spec["lanes"][0]["label"] == "North fan" and spec["lanes"][0]["stats"]["active_pct"] == 50.0
    assert spec["temperature"]["band"]["stats"]["inside"] > 0 and spec["kind"] == "timeline"
    assert m.build_timeline_spec("t", T0, T0 + H, [], None)["temperature"] is None
    page = m.timeline_html({**spec, "title": "</script><b>x"})
    assert "</script><b>" not in page and "innerHTML" not in page


# ---------------- number tiles ----------------

NOW = int(T0 * 1000)


def st(value, age_min=1, **attrs):
    return {"entity_id": "x.y", "state": str(value), "attributes": attrs, "last_reported": iso(T0 - age_min * 60),
            "last_updated": iso(T0 - age_min * 60), "last_changed": iso(T0 - 3 * H)}


def day_points(*values):
    n = len(values)
    return [[NOW - int((n - 1 - i) * 24 * H * 1000 / (n - 1)), v] for i, v in enumerate(values)]


def test_tile_inside_below_above_the_band_with_icons_and_text():
    pts = day_points(68.0, 69.0, 70.0, 71.0, 72.0)
    inside = m.build_tile("greenhouse_temperature", st(72, unit_of_measurement="°F"), pts, NOW, (65.0, 78.0))
    assert inside["status"] == {"level": "ok", "icon": "✓", "text": "Inside target 65 to 78 °F"} and inside["meter"]["value"] == 72
    low = m.build_tile("greenhouse_temperature", st(62.4, unit_of_measurement="°F"), pts, NOW, (65.0, 78.0))
    assert low["status"]["icon"] == "▼" and "Below target by 2.6 °F" in low["status"]["text"]
    high = m.build_tile("greenhouse_temperature", st(80, unit_of_measurement="°F"), pts, NOW, (65.0, 78.0))
    assert high["status"]["icon"] == "▲" and "Above target by 2.0" in high["status"]["text"]
    assert inside["value"] == "72.0" and inside["unit"] == "°F" and inside["spark"]["points"]


def test_tile_change_over_the_last_hour_uses_the_reading_an_hour_ago():
    pts = [[NOW - 5 * H * 1000, 60.0], [NOW - 2 * H * 1000, 62.0], [NOW - H * 1000 // 2, 66.0], [NOW, 66.0]]
    t = m.build_tile("outside_temperature", st(66), pts, NOW)
    assert t["delta"]["value"] == 4.0 and t["delta"]["text"].startswith("▲ 4.0")        # 62 an hour ago
    steady = m.build_tile("outside_temperature", st(62), pts, NOW)
    assert steady["delta"]["text"] == "Steady vs 1 h ago"
    short = m.build_tile("outside_temperature", st(66), [[NOW - 600_000, 66.0], [NOW, 66.0]], NOW)
    assert short["delta"] is None  # not enough history to say


def test_stale_and_unavailable_readings_are_flagged_not_trusted():
    stale = m.build_tile("greenhouse_temperature", st(70, age_min=95), day_points(70.0, 70.0), NOW, (65.0, 78.0))
    assert stale["stale"] and stale["status"]["level"] == "warn" and "95 min" in stale["status"]["text"] and stale["meter"] is None
    down = m.build_tile("greenhouse_temperature", st("unavailable"), [], NOW, (65.0, 78.0))
    assert down["kind"] == "state" and down["value"] == "No data" and down["status"]["level"] == "warn"


def test_state_tiles_and_humidity_set_point():
    mode = m.build_tile("mode_switch", st("Growing"), [], NOW)
    assert mode["kind"] == "state" and mode["value"] == "Growing" and mode["since"] == int((T0 - 3 * H) * 1000)
    hum = m.build_tile("greenhouse_humidity", st(61), day_points(60.0, 61.0), NOW, None, 70.0)
    assert hum["unit"] == "%" and hum["set_point"] == "Set point 70 %" and hum["status"] is None  # information only


def test_values_page_is_safe():
    page = m.values_html({"kind": "values", "title": "</script><img onerror=1>", "now": 0, "tiles": []})
    assert "</script><img" not in page and "innerHTML" not in page and "https://" not in page


# ---------------- the two tools end to end ----------------

EQ_ROLES = {**ROLES, "equipment_north_fan": [{"entity_id": "switch.north"}], "equipment_shutters": [{"entity_id": "cover.shutters"}],
            "mode_switch": [{"entity_id": "input_select.mode"}], "target_humidity": [{"entity_id": "input_number.rh"}]}


def eq_gateway():
    hist = {
        "switch.north": [(0, "off"), (3, "on"), (5, "off"), (9, "on"), (9.04, "off")],
        "cover.shutters": [(0, "closed"), (4, "open")],
        "sensor.gh_t": [(0, "70"), (6, "62"), (12, "75")], "sensor.low": [(0, "65")], "sensor.high": [(0, "78")],
        "sensor.gh_h": [(0, "60"), (24, "64")], "sensor.out_t": [(0, "40"), (20, "44")],
    }
    state = {"sensor.gh_t": ("72", "°F"), "sensor.gh_h": ("64", "%"), "sensor.out_t": ("44", "°F"), "sensor.low": ("65", "°F"),
             "sensor.high": ("78", "°F"), "input_select.mode": ("Growing", None), "input_number.rh": ("70", "%")}
    calls = []

    def transport(method, url, headers, body):
        u = urlparse(url)
        q = parse_qs(u.query)
        calls.append(u.path)
        if u.path == "/glossary/roles":
            return 200, json.dumps(EQ_ROLES).encode()
        if u.path == "/ha/states":
            wanted = q["entity_id"][0].split(",")
            return 200, json.dumps([{"entity_id": e, "state": state[e][0], "last_reported": iso(T0 - 120), "last_changed": iso(T0 - 6 * H),
                                     "attributes": {"unit_of_measurement": state[e][1]} if state[e][1] else {}}
                                    for e in wanted if e in state]).encode()
        if u.path == "/ha/history":
            eid = q["entity_id"][0]
            return 200, json.dumps(rows(eid, *[(T0 - 24 * H + h * H, v) for h, v in hist[eid]]) if eid in hist else []).encode()
        return 404, b'{"detail":"unexpected"}'

    return transport, calls


@pytest.fixture
def eq_tool():
    t = m.Tools()
    t.valves.GATEWAY_URL, t.valves.AS_OF = "http://gw:8099", m._iso(T0)
    transport, calls = eq_gateway()
    t._gw = lambda: m.Gateway("http://gw:8099", "k", as_of=t.valves.AS_OF, caller="open-webui", transport=transport)
    t.calls = calls
    return t


def test_show_timeline_uses_every_equipment_role_by_default(eq_tool):
    response, facts = eq_tool.show_timeline()
    assert response.headers["Content-Disposition"] == "inline"
    assert set(facts["equipment"]) == {"North fan", "Shutters"}
    fan = facts["equipment"]["North fan"]
    assert fan["starts"] == 2 and fan["short_runs"] == 1 and fan["active_minutes"] == 120 + 2.4
    assert facts["equipment"]["Shutters"]["now"] == "open"
    assert facts["greenhouse_temperature"]["target_band_time_pct"] is not None and "not why" in facts["note"]
    _, f1 = eq_tool.show_timeline("equipment_north_fan", with_temperature=False)
    assert "greenhouse_temperature" not in f1 and set(f1["equipment"]) == {"North fan"}


def test_show_timeline_refuses_instead_of_guessing(eq_tool):
    assert "Unknown role" in eq_tool.show_timeline("equipment_nope")
    eq_tool._gw = lambda: m.Gateway("http://gw:8099", "k", transport=lambda *a: (200, b'{"greenhouse_temperature":[{"entity_id":"sensor.gh_t"}]}'))
    assert "No equipment roles are approved" in eq_tool.show_timeline()


def test_show_values_defaults_to_the_usual_set_and_reports_figures(eq_tool):
    response, facts = eq_tool.show_values(title="Right now")
    assert response.headers["Content-Disposition"] == "inline"
    assert set(facts["values"]) == {"Greenhouse temperature", "Greenhouse humidity", "Outside temperature", "Mode switch"}  # no vpd role
    gh = facts["values"]["Greenhouse temperature"]
    assert gh["value"] == "72.0" and gh["unit"] == "°F" and gh["status"] == "Inside target 65 to 78 °F"
    assert facts["values"]["Greenhouse humidity"]["set_point"] == "Set point 70 %"
    assert facts["values"]["Mode switch"]["value"] == "Growing"
    assert b"Greenhouse temperature" in response.body and facts["no_state_for"] == []


def test_show_values_and_timeline_are_described_for_the_model():
    for name, params in (("show_timeline", ("roles", "hours", "with_temperature", "title")), ("show_values", ("roles", "title"))):
        doc = getattr(m.Tools, name).__doc__
        assert ":return:" in doc and all(f":param {p}:" in doc for p in params), name
