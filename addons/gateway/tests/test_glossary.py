import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from gateway.glossary import store, sync
from gateway.main import app
from gateway.metric_catalog import store as catalog_store
from gateway.metric_catalog.models import EntityRecord
from gateway.ui import PAGE

T0 = 1_800_000_000.0
OWNER = ("172.30.32.2", 50000)  # the Supervisor's ingress proxy


def make_catalog(path, extra=()):
    ents = {
        "sensor.indoor_outdoor_meter_0cc7_temperature": EntityRecord(
            "sensor.indoor_outdoor_meter_0cc7_temperature", friendly_name="GAHT Exit Temperature", unit="°F",
            device_class="temperature", area="Greenhouse", vm_status="ok", has_state=True),
        "sensor.gh_humidity": EntityRecord("sensor.gh_humidity", friendly_name="GH Humidity", unit="%", vm_status="ok"),
        "input_number.hot_alarm_temp": EntityRecord("input_number.hot_alarm_temp", friendly_name="Hot Alarm Temp",
                                                    vm_status="mirrored"),
        "sensor.disabled_thing": EntityRecord("sensor.disabled_thing", disabled=True, vm_status="ok"),
        "sensor.never_recorded": EntityRecord("sensor.never_recorded", vm_status="missing"),
        "sensor.hidden": EntityRecord("sensor.hidden", vm_status="ignored"),
        "automation.something": EntityRecord("automation.something", vm_status="ok"),
        "update.core": EntityRecord("update.core", vm_status="ok"),
        **dict(extra),
    }
    conn = catalog_store.connect(path)
    catalog_store.save_run(conn, "influxdb", ents, [])
    return conn


@pytest.fixture
def g(tmp_path):
    cat = make_catalog(tmp_path / "c.sqlite3")
    glossary = store.connect(str(tmp_path / "g.sqlite3"))
    store.sync(cat, glossary, {"input_number.hot_alarm_temp": ["automation.vent"]}, now=T0)
    yield glossary
    glossary.close()
    cat.close()


def test_cryptic_score_and_draft_text():
    assert store.cryptic_score("sensor.indoor_outdoor_meter_0cc7_temperature", "GAHT Exit Temperature") >= 0.5
    assert store.cryptic_score("sensor.gh_humidity", "GH Humidity") == 0.0
    assert store.cryptic_score("sensor.temperature_2", "Temperature") >= 0.2
    text = store.draft_text({"entity_id": "x.y", "friendly_name": "GAHT Exit Temperature", "unit": "°F",
                             "device_class": "temperature", "area": "Greenhouse"})
    assert text == "GAHT Exit Temperature. Temperature in °F located in Greenhouse."
    assert store.draft_text({"entity_id": "sensor.bare"}) == "sensor.bare."


def test_sync_drafts_only_eligible_entities_and_sorts_cryptic_first(g):
    entries = store.list_entries(g)
    assert entries[0]["entity_id"] == "sensor.indoor_outdoor_meter_0cc7_temperature"
    assert {e["entity_id"] for e in entries} == {"sensor.indoor_outdoor_meter_0cc7_temperature", "sensor.gh_humidity",
                                                  "input_number.hot_alarm_temp"}
    first = store.get(g, "sensor.indoor_outdoor_meter_0cc7_temperature")
    assert first["status"] == "draft" and first["source"] == "generated" and first["approved_by"] is None
    assert store.get(g, "input_number.hot_alarm_temp")["referenced_by"] == ["automation.vent"]


def test_resync_never_overwrites_owner_text_and_deactivates_gone_entities(tmp_path, g):
    store.owner_update(g, "sensor.gh_humidity", "Sam", "Humidity at basket height, zone 1", status="approved", now=T0 + 5)
    cat = make_catalog(tmp_path / "c2.sqlite3")
    cat.execute("UPDATE entities SET friendly_name='Renamed' WHERE entity_id='sensor.gh_humidity'")
    cat.execute("UPDATE entities SET vm_status='missing' WHERE entity_id='input_number.hot_alarm_temp'")
    out = store.sync(cat, g, now=T0 + 10)
    assert out == {"added": 0, "updated": 2, "deactivated": 1}
    kept = store.get(g, "sensor.gh_humidity")
    assert kept["meaning"] == "Humidity at basket height, zone 1" and kept["status"] == "approved"
    assert kept["friendly_name"] == "Renamed"  # facts refresh; the owner's words do not change
    assert not store.get(g, "input_number.hot_alarm_temp")["active"]
    assert "input_number.hot_alarm_temp" not in {e["entity_id"] for e in store.list_entries(g)}
    assert "input_number.hot_alarm_temp" in {e["entity_id"] for e in store.list_entries(g, active_only=False)}


def test_agent_can_only_draft(g):
    eid = "sensor.gh_humidity"
    out = store.propose(g, eid, "  Relative humidity inside the greenhouse  ", ["rh"], now=T0 + 1)
    assert out["meaning"] == "Relative humidity inside the greenhouse" and out["status"] == "draft"
    assert out["source"] == "agent" and out["approved_by"] is None and out["aliases"] == ["rh"]
    with pytest.raises(store.GlossaryError) as nope:
        store.propose(g, "sensor.unknown", "x")
    assert nope.value.code == 404
    with pytest.raises(store.GlossaryError, match="empty"):
        store.propose(g, eid, "   ")
    with pytest.raises(store.GlossaryError, match="longer"):
        store.propose(g, eid, "x" * 601)
    store.owner_update(g, eid, "Sam", status="approved", now=T0 + 2)
    with pytest.raises(store.GlossaryError) as locked:  # an approved entry is out of the agent's reach
        store.propose(g, eid, "changed by the agent")
    assert locked.value.code == 409 and store.get(g, eid)["meaning"].startswith("Relative humidity")


def test_owner_update_rules(g):
    eid = "sensor.gh_humidity"
    with pytest.raises(store.GlossaryError, match="status"):
        store.owner_update(g, eid, "Sam", status="maybe")
    store.propose(g, eid, "x", now=T0 + 1)
    blank = store.owner_update(g, eid, "Sam", meaning="", now=T0 + 2)
    assert blank["status"] == "draft"
    with pytest.raises(store.GlossaryError, match="empty meaning"):
        store.owner_update(g, eid, "Sam", status="approved")
    done = store.owner_update(g, eid, "Sam", meaning="Zone 1 RH", aliases=[" rh ", "", "humid"], status="approved", now=T0 + 3)
    assert (done["approved_by"], done["approved_at"], done["aliases"]) == ("Sam", T0 + 3, ["rh", "humid"])
    back = store.owner_update(g, eid, "Pat", status="draft", now=T0 + 4)
    assert back["approved_by"] is None and back["updated_by"] == "Pat"


def test_replays_only_see_what_was_approved_by_then(g):
    eid = "sensor.gh_humidity"
    store.owner_update(g, eid, "Sam", "Zone 1 RH", status="approved", now=T0 + 100)
    store.owner_update(g, eid, "Sam", "Zone 1 RH, basket height", status="approved", now=T0 + 200)
    store.owner_update(g, "input_number.hot_alarm_temp", "Sam", "Hot limit", status="approved", now=T0 + 150)
    store.owner_update(g, eid, "Sam", status="draft", now=T0 + 300)  # withdrawn later
    assert store.approved_as_of(g, T0 + 50) == []
    assert [e["meaning"] for e in store.approved_as_of(g, T0 + 120)] == ["Zone 1 RH"]
    at_250 = {e["entity_id"]: e["meaning"] for e in store.approved_as_of(g, T0 + 250)}
    assert at_250 == {eid: "Zone 1 RH, basket height", "input_number.hot_alarm_temp": "Hot limit"}
    assert [e["entity_id"] for e in store.approved_as_of(g, T0 + 350)] == ["input_number.hot_alarm_temp"]


# ---------------- who may do what over HTTP -----------------

@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOSSARY_DB", str(tmp_path / "g.sqlite3"))
    monkeypatch.setenv("GATEWAY_API_KEY", "s3cret")
    cat = make_catalog(tmp_path / "c.sqlite3")
    glossary = store.connect(str(tmp_path / "g.sqlite3"))
    store.sync(cat, glossary, now=T0)
    glossary.close()
    KEY = {"X-Gateway-Key": "s3cret"}
    return (TestClient(app, client=("172.30.33.5", 1234)), TestClient(app, client=OWNER), KEY)


EID = "sensor.gh_humidity"


def test_a_leaked_api_key_can_draft_but_never_approve(web):
    agent, _, key = web
    assert agent.get("/glossary", headers=key).status_code == 200
    draft = agent.post("/glossary/drafts", headers=key, json={"entity_id": EID, "meaning": "Zone 1 RH"})
    assert draft.status_code == 200 and draft.json()["status"] == "draft"
    for attempt in (
        agent.put(f"/glossary/{EID}", headers=key, json={"status": "approved"}),
        agent.put(f"/glossary/{EID}", headers={**key, "X-Ingress-Path": "/api/hassio_ingress/abc"}, json={"status": "approved"}),
        agent.put(f"/glossary/{EID}", headers={**key, "X-Forwarded-For": "172.30.32.2", "X-Remote-User-Name": "admin"},
                  json={"status": "approved"}),
    ):
        assert attempt.status_code == 403 and "owner only" in attempt.json()["detail"]
    assert agent.put(f"/glossary/{EID}", json={"status": "approved"}).status_code == 401  # no key at all
    assert agent.get(f"/glossary/{EID}", headers=key).json()["status"] == "draft"


def test_owner_via_ingress_can_approve_without_a_key_and_is_named(web):
    agent, owner, key = web
    assert owner.get("/glossary/whoami").json() == {"owner": True, "user": "owner"}
    assert agent.get("/glossary/whoami", headers=key).json() == {"owner": False, "user": None}
    named = owner.get("/glossary/whoami", headers={"X-Remote-User-Display-Name": "Sam"}).json()
    assert named["user"] == "Sam"
    done = owner.put(f"/glossary/{EID}", headers={"X-Remote-User-Display-Name": "Sam"},
                     json={"meaning": "Zone 1 RH", "status": "approved"})
    assert done.status_code == 200 and done.json()["approved_by"] == "Sam"
    locked = agent.post("/glossary/drafts", headers=key, json={"entity_id": EID, "meaning": "agent rewrite"})
    assert locked.status_code == 409
    assert owner.put("/glossary/not_an_id", json={"status": "approved"}).status_code in (404, 405)


def test_owner_edit_validation(web):
    _, owner, _ = web
    assert owner.put(f"/glossary/{EID}", json={"status": "approved", "meaning": ""}).status_code == 400
    assert owner.put(f"/glossary/{EID}", json={"status": "bogus"}).status_code == 422
    assert owner.put("/glossary/sensor.nope", json={"meaning": "x"}).status_code == 404


def test_other_writes_stay_refused_for_everyone(web):
    agent, owner, key = web
    for client, headers in ((agent, key), (owner, {})):
        assert client.delete(f"/glossary/{EID}", headers=headers).status_code == 405
        assert client.post(f"/glossary/{EID}", headers=headers, json={}).status_code == 405
        assert client.put("/ha/services", headers=headers, json={}).status_code == 405
        assert client.post("/glossary/approve", headers=headers, json={}).status_code == 405


def test_glossary_reads_honour_as_of(web, tmp_path):
    agent, owner, key = web
    owner.put(f"/glossary/{EID}", json={"meaning": "Zone 1 RH", "status": "approved"})
    import time
    now = time.time()
    live = agent.get("/glossary", headers=key, params={"status": "approved"}).json()
    assert [e["entity_id"] for e in live] == [EID]
    before = agent.get("/glossary", headers={**key, "X-As-Of": str(now - 3600)}).json()
    assert before == []
    assert agent.get(f"/glossary/{EID}", headers={**key, "X-As-Of": str(now - 3600)}).status_code == 404
    after = agent.get(f"/glossary/{EID}", headers={**key, "X-As-Of": str(now + 60)}).json()
    assert after["meaning"] == "Zone 1 RH" and set(after) >= {"approved_at", "approved_by"}


def test_calls_record_whether_they_came_from_an_owner(web, tmp_path):
    agent, owner, key = web
    agent.get("/glossary", headers=key)
    owner.get("/glossary")
    rows = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert [r["owner"] for r in rows] == [False, True]


def test_panel_page_is_self_contained(web):
    _, owner, _ = web
    page = owner.get("/")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "Entity glossary" in page.text
    assert "http://" not in PAGE and "https://" not in PAGE and 'src="/' not in PAGE  # relative URLs only: works under ingress
    assert 'fetch("/' not in PAGE and "innerHTML" not in PAGE  # entity names are shown as text, never as markup


def test_sync_from_env_uses_the_catalog_database(tmp_path, monkeypatch):
    make_catalog(tmp_path / "c.sqlite3").close()
    monkeypatch.setenv("CATALOG_DB", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("GLOSSARY_DB", str(tmp_path / "g.sqlite3"))
    monkeypatch.setenv("MIRROR_DIR", str(tmp_path / "no-mirror"))
    assert sync.sync_from_env()["added"] == 3


def test_ingress_is_configured():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    assert cfg["ingress"] is True and cfg["ingress_port"] == 8099 and cfg["panel_admin"] is True
