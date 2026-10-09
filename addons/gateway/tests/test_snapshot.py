import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from gateway import deps
from gateway.jobs import helper_snapshot as hs
from gateway.jobs.scheduler import start_scheduler
from gateway.main import app
from gateway.metric_catalog import store
from gateway.metric_catalog.models import EntityRecord, SeriesRecord

NOW = 1_800_000_000


def st(eid, state, unit=None):
    attrs = {"unit_of_measurement": unit} if unit else {}
    return {"entity_id": eid, "state": state, "attributes": attrs}


STATES = [
    st("input_number.hot_alarm_temp", "82.5", "°F"),
    st("input_number.leaf_offset", "-1.5"),
    st("input_number.humidity", "55", "%"),
    st("input_boolean.heater", "on"),
    st("input_boolean.off_one", "off"),
    st("input_number.broken", "unavailable", "°F"),
    st("sensor.not_a_helper", "5", "W"),       # wrong domain, even though the pattern matches it
    st("input_number.unlisted", "1"),          # matches no pattern
]
PATTERNS = ["input_number.*", "input_boolean.*", "sensor.*"]


def test_plan_follows_the_home_assistant_convention():
    result = hs.plan(STATES, ["input_number.hot*", "input_number.leaf*", "input_number.hum*",
                              "input_boolean.*", "input_number.broken", "sensor.*"], now=NOW)
    assert result["lines"] == [
        f"°F,domain=input_number,entity_id=hot_alarm_temp value=82.5 {NOW}",
        f"input_number.leaf_offset,domain=input_number,entity_id=leaf_offset value=-1.5 {NOW}",
        f"%,domain=input_number,entity_id=humidity value=55.0 {NOW}",
        f"input_boolean.heater,domain=input_boolean,entity_id=heater value=1.0 {NOW}",
        f"input_boolean.off_one,domain=input_boolean,entity_id=off_one value=0.0 {NOW}",
    ]
    assert result["skipped"] == [{"entity_id": "input_number.broken", "reason": "state 'unavailable' is not numeric"}]


def test_only_listed_helpers_and_empty_list_writes_nothing():
    assert hs.plan(STATES, [], now=NOW)["lines"] == []
    assert hs.plan(STATES, ["  ", ""], now=NOW)["lines"] == []
    only = hs.plan(STATES, ["input_number.humidity"], now=NOW)["lines"]
    assert len(only) == 1 and only[0].startswith("%,")


def test_never_writes_outside_the_supported_domains():
    states = [st("sensor.x", "5", "W"), st("light.x", "on"), st("switch.pump", "on"), st("climate.x", "20")]
    assert hs.plan(states, ["*"], now=NOW)["lines"] == []


def test_escaping_special_characters():
    line = hs.plan([st("input_number.a", "1", "Signal %, rel=x")], ["*"], now=NOW)["lines"][0]
    assert line.startswith("Signal\\ %\\,\\ rel=x,domain=input_number")  # '=' only needs escaping in tags


def _catalog(tmp_path, extra_series=()):
    """A catalog shaped like the real one: most series carry db=homeassistant."""
    conn = store.connect(tmp_path / "c.sqlite3")
    series, ents = [], {}
    for i in range(9):
        eid = f"sensor.s{i}"
        ents[eid] = EntityRecord(eid)
        series.append(SeriesRecord(eid, "W_value", {"entity_id": f"s{i}", "domain": "sensor", "db": "homeassistant"},
                                   "{}", last_seen=5, field="value", kind="value"))
    for eid, metric, labels in extra_series:
        ents.setdefault(eid, EntityRecord(eid))
        series.append(SeriesRecord(eid, metric, labels, "{}", last_seen=5, field="value", kind="value"))
    store.save_run(conn, "influxdb", ents, series)
    return conn


def test_shared_tags_are_added_and_one_off_tags_dropped(tmp_path):
    odd = ("input_number.gh_hyst", "°F_value",
           {"entity_id": "gh_hyst", "domain": "input_number", "db": "homeassistant", "friendly_name": "GH Hyst"})
    conn = _catalog(tmp_path, [odd])
    assert store.latest_run(conn) is not None
    assert hs.common_tags(conn) == {"db": "homeassistant"}  # friendly_name is on 1 of 10 series: dropped
    result = hs.plan([st("input_number.gh_hyst", "1", "°F"), st("input_number.new_one", "2", "kPa")],
                     ["input_number.*"], conn, now=NOW)
    assert result["lines"] == [
        f"°F,db=homeassistant,domain=input_number,entity_id=gh_hyst value=1.0 {NOW}",
        f"kPa,db=homeassistant,domain=input_number,entity_id=new_one value=2.0 {NOW}",  # no series yet: gets db too
    ]
    checks = {c["entity_id"]: c["check"] for c in result["checks"]}
    assert checks == {"input_number.gh_hyst": "matches existing series", "input_number.new_one": "new series"}


def test_helper_is_skipped_when_its_name_would_differ_from_existing_series(tmp_path):
    conn = _catalog(tmp_path, [("input_number.x", "degF_value", {"entity_id": "x", "domain": "input_number",
                                                                   "db": "homeassistant"})])
    result = hs.plan([st("input_number.x", "1", "°F")], ["*"], conn, now=NOW)
    assert result["lines"] == []
    assert "history is not split" in result["skipped"][0]["reason"]
    assert result["checks"][0]["check"] == "DIFFERS"


def test_run_refuses_without_a_catalog(tmp_path, vm):
    router, client = vm
    with pytest.raises(RuntimeError, match="run the catalog first"):
        hs.run(HA(), client, ["input_number.humidity"], str(tmp_path / "empty.sqlite3"))
    assert router.calls.call_count == 0


def test_run_with_a_catalog_writes_the_shared_tags(tmp_path, vm):
    router, client = vm
    _catalog(tmp_path).close()
    out = hs.run(HA(), client, ["input_number.humidity"], str(tmp_path / "c.sqlite3"))
    assert out["common_tags"] == {"db": "homeassistant"}
    assert router.calls.last.request.content.decode().startswith("%,db=homeassistant,domain=input_number")


@pytest.fixture
def vm():
    with respx.mock(base_url="http://vm", assert_all_called=False) as router:
        router.post("/write").respond(204)
        yield router, httpx.Client(base_url="http://vm")


class HA:
    def states(self):
        return STATES


def test_run_posts_line_protocol_to_victoriametrics(vm):
    router, client = vm
    out = hs.run(HA(), client, ["input_number.humidity", "input_boolean.heater"])
    req = router.calls.last.request
    assert req.method == "POST" and req.url.path == "/write" and req.url.params["precision"] == "s"
    assert out["written"] == 2 and len(req.content.decode().strip().splitlines()) == 2


def test_run_failure_modes(vm):
    router, client = vm
    router.post("/write").respond(401)
    with pytest.raises(RuntimeError, match="credentials for writing"):
        hs.run(HA(), client, ["input_number.humidity"])
    router.post("/write").respond(400, text="bad line")
    with pytest.raises(RuntimeError, match="HTTP 400"):
        hs.run(HA(), client, ["input_number.humidity"])
    with pytest.raises(RuntimeError, match="InfluxDB-style"):
        hs.run(HA(), client, ["input_number.humidity"], ingest_mode="prometheus")


def test_nothing_is_posted_when_there_is_nothing_to_write(vm):
    router, client = vm
    hs.run(HA(), client, [])
    assert router.calls.call_count == 0


def test_forced_as_of_blocks_writing(monkeypatch):
    monkeypatch.setenv("GATEWAY_AS_OF", "1700000000")
    assert "skipped" in hs.run_from_env()


def test_preview_route_is_get_only_and_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("CATALOG_DB", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("HELPER_SNAPSHOT_ENTITIES", "input_number.humidity")
    app.dependency_overrides[deps.get_ha] = lambda: HA()
    client = TestClient(app)
    body = client.get("/catalog/snapshot-preview").json()
    assert body["enabled"] is False and len(body["lines"]) == 1 and body["patterns"] == ["input_number.humidity"]
    assert client.post("/catalog/snapshot-preview").status_code == 405
    assert client.get("/catalog/snapshot-preview", headers={"X-As-Of": "1700000000"}).status_code == 409
    paths = app.openapi()["paths"]
    assert [p for p, ops in paths.items() if "post" in ops] == ["/catalog/run"]  # no new write trigger


def test_scheduler_only_adds_the_snapshot_job_when_asked():
    sched = start_scheduler("15 3 * * *", dict)
    try:
        assert {j.id for j in sched.get_jobs()} <= {"metric_catalog", "metric_catalog_startup"}
    finally:
        sched.shutdown(wait=False)
    sched = start_scheduler("15 3 * * *", dict, snapshot_cron="5 */6 * * *", run_snapshot=dict)
    try:
        assert "helper_snapshot" in {j.id for j in sched.get_jobs()}
    finally:
        sched.shutdown(wait=False)
