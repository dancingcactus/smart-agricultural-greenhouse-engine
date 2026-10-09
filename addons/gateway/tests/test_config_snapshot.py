import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gateway import deps
from gateway.main import app
from gateway.snapshot import automations, files, gitmirror, secrets
from gateway.snapshot import run as snapshot_run

T0 = 1_800_000_000


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


AUTOMATIONS = """\
- id: "1001"
  alias: Vent when hot
  description: Opens the vent above the hot setpoint
  mode: single
  triggers:
    - trigger: numeric_state
      entity_id: sensor.gh_temperature
      above: input_number.hot_alarm_temp
  conditions:
    - condition: state
      entity_id: input_boolean.mister
      state: "off"
  actions:
    - action: cover.open_cover
      target: {entity_id: cover.vent}
    - if:
        - condition: template
          value_template: "{{ states('sensor.outside_temperature') | float > 80 }}"
      then:
        - action: switch.turn_on
          target: {entity_id: switch.second_fan}
- id: "1002"
  alias: Frost Alarm
  trigger:
    - platform: numeric_state
      entity_id: sensor.gh_temperature
      below: input_number.cold_alarm_temp
  action:
    - service: notify.mobile_app
      data: {message: "Cold! !secret ignored here"}
"""

CONFIGURATION = """\
homeassistant:
  name: Greenhouse
  latitude: 40.1234
  longitude: -105.5678
  elevation: 1600
influxdb:
  host: db.local
  password: hunter2-influx
  api_key: !secret influx_key
mqtt:
  broker: 192.168.1.5
  username: mqtt
  password: !secret mqtt_password
"""

SECRETS = "influx_key: abcd-1234-efgh\nmqtt_password: correct horse battery\nwifi_pin: 42\n"


@pytest.fixture
def cfg(tmp_path):
    root = tmp_path / "config"
    write(root, "configuration.yaml", CONFIGURATION)
    write(root, "automations.yaml", AUTOMATIONS)
    write(root, "secrets.yaml", SECRETS)
    write(root, "packages/heating.yaml", "automation:\n  - id: p1\n    alias: Heater on\n    triggers: []\n    actions:\n"
                                          "      - action: switch.turn_on\n        target: {entity_id: switch.heater}\n")
    write(root, ".storage/auth", '{"data": {"refresh_tokens": [{"token": "SHOULD-NEVER-COPY"}]}}')
    write(root, ".storage/lovelace", '{"data": {"config": {"title": "GH", "api_key": "dash-key-12345"}}}')
    write(root, ".storage/input_number", '{"data": {"items": [{"id": "hot_alarm_temp", "name": "Hot"}]}}')
    write(root, ".storage/core.config_entries", '{"data": {"entries": [{"data": {"password": "zzz"}}]}}')
    write(root, "custom_components/x/key.pem", "-----BEGIN PRIVATE KEY-----")
    write(root, "home-assistant.log", "token=abc")
    return root


# ---------------- what is copied -----------------

def test_allowlist_and_hard_denials():
    for ok in ("configuration.yaml", "automations.yaml", "packages/a/b.yaml", "blueprints/automation/x/y.yaml",
               ".storage/input_number", ".storage/lovelace", ".storage/lovelace.dashboard_x"):
        assert files.is_allowed(ok), ok
    for bad in ("secrets.yaml", "packages/secrets.yaml", ".storage/auth", ".storage/auth_provider.homeassistant",
                ".storage/core.config_entries", "x/server.pem", "home-assistant.log", "deps/foo.yaml",
                ".storage/core.entity_registry", "packages/api_token.yaml", "known_devices.yaml", ".cloud/x.yaml"):
        assert not files.is_allowed(bad), bad


def test_copy_skips_denied_files_symlinks_and_large_files(cfg, tmp_path):
    outside = tmp_path / "outside.yaml"
    outside.write_text("secret: outside")
    (cfg / "link.yaml").symlink_to(outside)
    write(cfg, "big.yaml", "a: " + "x" * (files.MAX_FILE_BYTES + 10))
    staging = tmp_path / "stage"
    report = files.copy_allowlisted(cfg, staging)
    copied = {r["path"] for r in report if "skipped" not in r}
    assert {"configuration.yaml", "automations.yaml", "packages/heating.yaml", ".storage/lovelace",
            ".storage/input_number"} <= copied
    assert not (staging / "secrets.yaml").exists() and not (staging / ".storage/auth").exists()
    assert not (staging / "link.yaml").exists() and not (staging / "home-assistant.log").exists()
    assert {"path": "big.yaml", "skipped": "larger than 2 MB"} in report


def test_yaml_redaction():
    text = ("a:\n  password: hunter2\n  api_key: !secret k\n  - token: abc\n  note: fine\n"
            "  private_key: |\n    line1\n    line2\n  after: kept\n  latitude: 12.5\n  - password: 'x y'\n")
    out, n = files.redact_yaml(text)
    assert "hunter2" not in out and "abc" not in out and "line1" not in out and "12.5" not in out and "x y" not in out
    assert "api_key: !secret k" in out and "note: fine" in out and "after: kept" in out
    assert n == 5
    assert out.count("[redacted]") == 6  # four plain keys and the two lines of the block scalar (its key keeps the |)


def test_json_redaction_and_unparseable_files():
    out, n = files.redact_json('{"a": {"api_key": "k123456", "title": "ok"}}')
    assert "k123456" not in out and '"title": "ok"' in out and n == 1
    assert files.redact_json("{not json")[0].strip() == "[redacted]"


# ---------------- secret scan -----------------

def test_secret_values_and_needles(cfg):
    values = secrets.load_secret_values(cfg)
    assert set(values) == {"abcd-1234-efgh", "correct horse battery", "42"}
    found, short = secrets.needles(values)
    assert short == 1 and "abcd-1234-efgh" in found and "correct%20horse%20battery" in found  # url-encoded form too


def test_scan_reports_paths_and_ids_never_values(tmp_path):
    write(tmp_path, "a/ok.txt", "nothing here")
    write(tmp_path, "a/bad.txt", "url: https://x/?k=abcd-1234-efgh")
    hits = secrets.scan([tmp_path / "a"], ["abcd-1234-efgh"])
    assert hits == [{"path": "bad.txt", "needle_id": hits[0]["needle_id"]}] and "abcd" not in json.dumps(hits)
    assert secrets.scan([tmp_path / "a"], []) == []


# ---------------- git mirror -----------------

def test_gitmirror_commits_only_changes_and_reads_history(tmp_path):
    repo, stage = tmp_path / "repo", tmp_path / "stage"
    write(stage, "a.yaml", "v: 1\n")
    write(stage, "gone.yaml", "x: 1\n")
    first = gitmirror.sync_and_commit(stage, repo, "one", T0)
    assert first and gitmirror.sync_and_commit(stage, repo, "again", T0 + 10) is None  # no empty commit
    write(stage, "a.yaml", "v: 2\n")
    (stage / "gone.yaml").unlink()
    second = gitmirror.sync_and_commit(stage, repo, "two", T0 + 3600)
    assert gitmirror.commit_at(repo, T0 + 100) == first and gitmirror.commit_at(repo, T0 + 3600) == second
    assert gitmirror.commit_at(repo, T0 - 1) is None and gitmirror.commit_at(repo, None) == second
    assert gitmirror.read_file(repo, first, "a.yaml") == b"v: 1\n" and gitmirror.read_file(repo, second, "gone.yaml") is None
    assert gitmirror.list_files(repo, first) == ["a.yaml", "gone.yaml"] and gitmirror.list_files(repo, second) == ["a.yaml"]
    assert [c["message"] for c in gitmirror.commits(repo)] == ["two", "one"]
    assert [c["message"] for c in gitmirror.commits(repo, "gone.yaml")] == ["two", "one"]
    assert gitmirror.commits(tmp_path / "empty") == []


def test_gitmirror_rejects_unsafe_paths(tmp_path):
    for bad in ("../x", "/etc/passwd", "a/../../b", "a;rm", ""):
        with pytest.raises(ValueError, match="bad path"):
            gitmirror.safe_path(bad)


# ---------------- automation catalog -----------------

STATES = [
    {"entity_id": "automation.vent_when_hot", "state": "on",
     "attributes": {"id": "1001", "friendly_name": "Vent when hot", "last_triggered": "2026-10-01T10:00:00+00:00"}},
    {"entity_id": "automation.frost_alarm", "state": "on", "attributes": {"id": "1002", "friendly_name": "Frost Alarm"}},
    *({"entity_id": e, "state": "1", "attributes": {}} for e in (
        "sensor.gh_temperature", "sensor.outside_temperature", "input_number.hot_alarm_temp",
        "input_number.cold_alarm_temp", "input_boolean.mister", "cover.vent", "switch.second_fan", "switch.heater")),
]
REGISTRY = [{"entity_id": "automation.vent_when_hot", "labels": ["agent_managed"]},
            {"entity_id": "automation.frost_alarm", "labels": ["agent_managed"]}]
LABELS = {"agent_managed": "agent-managed"}


def test_automation_catalog(cfg, tmp_path):
    stage = tmp_path / "stage"
    files.copy_allowlisted(cfg, stage)
    built = automations.build(stage, STATES, REGISTRY, LABELS, ["automation.frost_*"])
    by_alias = {a["alias"]: a for a in built["automations"]}
    vent, frost, heater = by_alias["Vent when hot"], by_alias["Frost Alarm"], by_alias["Heater on"]
    assert vent["entity_id"] == "automation.vent_when_hot" and vent["enabled"] is True and vent["managed"] is True
    assert vent["last_triggered"] == "2026-10-01T10:00:00+00:00" and vent["source_file"] == "automations.yaml"
    assert vent["triggers"][0]["type"] == "numeric_state"
    assert vent["services"] == ["cover.open_cover", "switch.turn_on"]  # found inside the nested "if/then"
    assert set(vent["entities"]) == {"sensor.gh_temperature", "input_number.hot_alarm_temp", "input_boolean.mister",
                                     "cover.vent", "sensor.outside_temperature", "switch.second_fan"}
    assert vent["condition_count"] == 1 and vent["description"].startswith("Opens the vent")
    # legacy keys (trigger/platform/action/service) are understood too
    assert frost["services"] == ["notify.mobile_app"] and frost["triggers"][0]["type"] == "numeric_state"
    # the protected pattern wins over the agent-managed label
    assert frost["protected"] is True and frost["managed"] is False and "agent-managed" in frost["labels"]
    assert heater["source_file"] == "packages/heating.yaml" and heater["in_runtime"] is False and heater["entity_id"] is None
    refs = built["entity_references"]
    assert refs["sensor.gh_temperature"] == ["automation.frost_alarm", "automation.vent_when_hot"]
    assert refs["switch.heater"] == ["config:p1"] and "notify.mobile_app" not in refs  # services are not entities


def test_unparseable_automation_file_is_reported(tmp_path):
    write(tmp_path, "automations.yaml", "- alias: [unclosed\n")
    found, problems = automations.find_automations(tmp_path)
    assert found == [] and problems[0]["path"] == "automations.yaml" and "YAML error" in problems[0]["problem"]


# ---------------- end to end -----------------

class FakeHA:
    def states(self):
        return STATES

    def ws_list(self, cmd):
        return {"config/label_registry/list": [{"label_id": "agent_managed", "name": "agent-managed"}],
                "config/entity_registry/list": REGISTRY}[cmd]


def test_snapshot_end_to_end(cfg, tmp_path):
    mirror = tmp_path / "data" / "mirror"
    result = snapshot_run.run(FakeHA(), cfg, mirror, [], now=T0)
    assert result["ok"] and result["changed"] and result["automations"] == 3
    assert result["secrets_checked"] > 0 and result["short_secrets_not_searched"] == 1
    tree = gitmirror.list_files(mirror, gitmirror.head(mirror))
    assert "configuration.yaml" in tree and "catalog/automations.json" in tree
    assert not any("secrets.yaml" in t or "auth" in t or "config_entries" in t or t.endswith(".pem") for t in tree)
    mirrored = gitmirror.read_file(mirror, "HEAD", "configuration.yaml").decode()
    assert "hunter2" not in mirrored and "40.1234" not in mirrored and "!secret mqtt_password" in mirrored
    # nothing changed: no new commit
    again = snapshot_run.run(FakeHA(), cfg, mirror, [], now=T0 + 3600)
    assert again["ok"] and again["changed"] is False and len(gitmirror.commits(mirror)) == 1
    # an edit becomes a second commit, and the first can still be read as of its time
    write(cfg, "automations.yaml", AUTOMATIONS.replace("Vent when hot", "Vent when warm"))
    third = snapshot_run.run(FakeHA(), cfg, mirror, [], now=T0 + 7200)
    assert third["changed"] and len(gitmirror.commits(mirror)) == 2
    old = gitmirror.read_file(mirror, gitmirror.commit_at(mirror, T0 + 100), "automations.yaml").decode()
    assert "Vent when hot" in old and "Vent when warm" not in old


def test_a_leaked_secret_aborts_and_leaves_the_mirror_untouched(cfg, tmp_path):
    mirror = tmp_path / "data" / "mirror"
    snapshot_run.run(FakeHA(), cfg, mirror, [], now=T0)
    before = gitmirror.head(mirror)
    # A secret pasted somewhere the key-name redaction cannot see: inside an ordinary value.
    write(cfg, "packages/notify.yaml", "notify:\n  url: https://hooks.example/send?key=abcd-1234-efgh\n")
    with pytest.raises(snapshot_run.SecretFound) as exc:
        snapshot_run.run(FakeHA(), cfg, mirror, [], now=T0 + 60)
    assert exc.value.hits[0]["path"] == "packages/notify.yaml" and "abcd" not in json.dumps(exc.value.hits)
    assert gitmirror.head(mirror) == before and not (mirror / "packages/notify.yaml").exists()
    status = json.loads((mirror.parent / "snapshot_status.json").read_text())
    assert status["ok"] is False and "abcd-1234-efgh" not in json.dumps(status)


def test_gateway_own_credentials_are_scanned_for(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_API_KEY", "gw-key-98765")
    write(cfg, "packages/oops.yaml", "note: gw-key-98765\n")
    with pytest.raises(snapshot_run.SecretFound):
        snapshot_run.run(FakeHA(), cfg, tmp_path / "m", [], now=T0)


def test_catalog_exports_are_scanned_too(cfg, tmp_path):
    out = tmp_path / "catalog_out"
    write(out, "entity_metrics.csv", "sensor.x,correct horse battery\n")
    with pytest.raises(snapshot_run.SecretFound) as exc:
        snapshot_run.run(FakeHA(), cfg, tmp_path / "m", [], extra_scan_roots=[out], now=T0)
    assert exc.value.hits[0]["path"] == "entity_metrics.csv"


def test_missing_config_directory_is_explained(monkeypatch, tmp_path):
    monkeypatch.setenv("HA_CONFIG_DIR", str(tmp_path / "nope"))
    with pytest.raises(RuntimeError, match="config directory not found"):
        snapshot_run.find_config_dir()


# ---------------- API -----------------

@pytest.fixture
def api(cfg, tmp_path, monkeypatch):
    monkeypatch.setenv("MIRROR_DIR", str(tmp_path / "data" / "mirror"))
    monkeypatch.setenv("HA_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("CATALOG_OUT_DIR", raising=False)
    app.dependency_overrides[deps.get_ha] = lambda: FakeHA()
    monkeypatch.setattr(deps, "get_ha", lambda: FakeHA())
    return TestClient(app)


def test_api_before_and_after_a_snapshot(api, cfg):
    assert api.get("/snapshot/files").status_code == 404
    assert api.get("/snapshot/status").json()["snapshots"] == 0
    run = api.post("/snapshot/run")
    assert run.status_code == 200 and run.json()["ok"]
    assert api.get("/snapshot/status").json()["snapshots"] == 1
    assert "automations.yaml" in api.get("/snapshot/files").json()["files"]
    body = api.get("/snapshot/file", params={"path": "configuration.yaml"})
    assert body.status_code == 200 and "hunter2" not in body.text and "X-Snapshot-Commit" in body.headers
    assert api.get("/snapshot/file", params={"path": "secrets.yaml"}).status_code == 404
    for bad in ("../x", "/etc/passwd"):
        assert api.get("/snapshot/file", params={"path": bad}).status_code == 400
    names = [a["alias"] for a in api.get("/snapshot/automations").json()]
    assert "Vent when hot" in names
    assert api.get("/snapshot/automations/automation.vent_when_hot").json()["managed"] is True
    assert api.get("/snapshot/automations/nope").status_code == 404
    assert api.get("/snapshot/automations", params={"managed": "true"}).json()
    refs = api.get("/snapshot/entity-references", params={"entity_id": "cover.vent"}).json()
    assert refs == ["automation.vent_when_hot"]
    assert api.get("/snapshot/log").json()[0]["message"].startswith("config snapshot")


def test_api_reads_honour_as_of(api, cfg):
    api.post("/snapshot/run")
    commit_ts = api.get("/snapshot/log").json()[0]["ts"]
    assert api.get("/snapshot/files", headers={"X-As-Of": str(commit_ts - 5)}).status_code == 404
    assert api.get("/snapshot/files", headers={"X-As-Of": str(commit_ts + 5)}).status_code == 200
    write(cfg, "automations.yaml", AUTOMATIONS.replace("Vent when hot", "Vent when warm"))
    subprocess.run(["sleep", "1.1"], check=True)  # commits are stamped with whole seconds
    api.post("/snapshot/run")
    old = api.get("/snapshot/file", params={"path": "automations.yaml"}, headers={"X-As-Of": str(commit_ts + 0.5)})
    assert "Vent when hot" in old.text and "Vent when warm" not in old.text


def test_api_reports_a_leak_without_the_value(api, cfg):
    write(cfg, "packages/notify.yaml", "notify:\n  url: https://x/?key=abcd-1234-efgh\n")
    resp = api.post("/snapshot/run")
    assert resp.status_code == 422 and "abcd" not in resp.text
    assert resp.json()["detail"]["hits"][0]["path"] == "packages/notify.yaml"
    assert api.get("/snapshot/status").json()["ok"] is False
