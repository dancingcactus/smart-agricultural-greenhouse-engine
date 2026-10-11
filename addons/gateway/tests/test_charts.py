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
