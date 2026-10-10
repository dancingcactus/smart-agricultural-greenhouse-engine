"""The agent side: the sandbox and Open WebUI add-ons, the gateway client, and the chat tool."""

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from gateway import deps
from gateway.glossary import store as glossary_store
from gateway.main import app
from gateway.metric_catalog import store as catalog_store
from gateway.metric_catalog.models import EntityRecord, SeriesRecord

ROOT = Path(__file__).resolve().parents[3]
SANDBOX, WEBUI = ROOT / "addons" / "open-terminal", ROOT / "addons" / "open-webui"
SDK = SANDBOX / "sdk" / "greenhouse_gateway.py"
TOOL = ROOT / "owui" / "tools" / "greenhouse_gateway.py"
ADDONS = [SANDBOX, WEBUI]


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def config_of(addon: Path) -> dict:
    return yaml.safe_load((addon / "config.yaml").read_text())


# ---------------- the add-ons cannot reach Home Assistant -----------------

FORBIDDEN = ("homeassistant_api", "hassio_api", "auth_api", "hassio_role", "host_network", "host_pid", "host_dbus",
             "full_access", "docker_api", "map", "devices", "udev", "usb", "uart", "gpio", "kernel_modules", "ingress")


@pytest.mark.parametrize("addon", ADDONS, ids=lambda p: p.name)
def test_no_home_assistant_credential_or_access(addon):
    cfg = config_of(addon)
    assert [k for k in FORBIDDEN if k in cfg] == []  # no token, no folder, no host access, nothing else
    assert set(cfg["options"]) <= set(cfg["schema"])


@pytest.mark.parametrize("addon", ADDONS, ids=lambda p: p.name)
def test_every_option_is_explained_on_the_settings_screen(addon):
    ui = yaml.safe_load((addon / "translations" / "en.yaml").read_text())["configuration"]
    assert set(ui) == set(config_of(addon)["schema"])
    assert all(v["name"] and len(v["description"]) > 40 for v in ui.values())


def test_sandbox_can_be_neither_reached_from_outside_nor_start_unconfined():
    cfg = config_of(SANDBOX)
    assert "ports" not in cfg and cfg["privileged"] == ["NET_ADMIN"] and cfg["init"] is False
    for required in ("api_key", "gateway_host", "gateway_key"):
        assert cfg["schema"][required] in ("str", "password")  # not optional: it will not start without them


def test_webui_exposes_only_its_own_port_and_pins_its_version():
    cfg = config_of(WEBUI)
    assert cfg["ports"] == {"8080/tcp": 8080}
    assert re.search(r"open-webui:v\d+\.\d+\.\d+\s*$", re.search(r"ARG BUILD_FROM=(.*)", (WEBUI / "Dockerfile").read_text()).group(0))


def test_dockerfiles_wire_up_the_wrappers():
    sandbox = (SANDBOX / "Dockerfile").read_text()
    assert '"/usr/bin/tini", "--", "/run.sh"' in sandbox and "sdk/greenhouse_gateway.py" in sandbox
    assert "options_to_env.py" in (WEBUI / "Dockerfile").read_text()


def test_start_scripts_are_valid_shell_and_stop_on_bad_options():
    assert subprocess.run(["sh", "-n", str(SANDBOX / "run.sh")], check=False).returncode == 0
    assert subprocess.run(["bash", "-n", str(WEBUI / "run.sh")], check=False).returncode == 0
    for script in ("run.sh",):
        assert "set -e" in (SANDBOX / script).read_text() and "|| exit 1" in (SANDBOX / script).read_text()
    assert "|| exit 1" in (WEBUI / "run.sh").read_text()


# ---------------- options become environment -----------------

sandbox_env = load(SANDBOX / "options_to_env.py", "sandbox_options_to_env")
webui_env = load(WEBUI / "options_to_env.py", "webui_options_to_env")

GOOD_SANDBOX = {"api_key": "k" * 20, "gateway_host": "abc-greenhouse-gateway", "gateway_port": 8099,
                "gateway_key": "gw-key", "extra_allowed_domains": ["pypi.local"]}
GOOD_WEBUI = {"openrouter_api_key": "sk-or-123", "terminal_host": "abc-greenhouse-sandbox", "terminal_port": 8000,
              "terminal_api_key": "k" * 20, "allow_signup": True, "allowed_models": []}


def test_sandbox_may_reach_only_the_gateway_and_what_is_listed():
    env = sandbox_env.build_env(GOOD_SANDBOX)
    assert env["OPEN_TERMINAL_ALLOWED_DOMAINS"] == "abc-greenhouse-gateway,pypi.local"
    assert env["GATEWAY_URL"] == "http://abc-greenhouse-gateway:8099" and env["GATEWAY_KEY"] == "gw-key"
    only = sandbox_env.build_env({**GOOD_SANDBOX, "extra_allowed_domains": []})
    assert only["OPEN_TERMINAL_ALLOWED_DOMAINS"] == "abc-greenhouse-gateway"  # never empty, never unset


@pytest.mark.parametrize("change", [
    {"gateway_host": ""}, {"gateway_host": "http://abc"}, {"gateway_host": "abc:8099"}, {"gateway_host": "a/b"},
    {"gateway_host": "a b"}, {"api_key": "short"}, {"api_key": ""}, {"gateway_key": ""},
    {"extra_allowed_domains": ["a,b"]}, {"extra_allowed_domains": ["https://x"]},
])
def test_sandbox_refuses_unsafe_or_incomplete_options(change):
    with pytest.raises(ValueError):
        sandbox_env.build_env({**GOOD_SANDBOX, **change})


def test_webui_environment():
    env = webui_env.build_env({**GOOD_WEBUI, "allowed_models": ["anthropic/claude-haiku-4.5", " ", "x/y"]})
    assert env["ENABLE_PERSISTENT_CONFIG"] == "false" and env["WEBUI_AUTH"] == "true"
    assert env["OPENAI_API_BASE_URLS"] == "https://openrouter.ai/api/v1" and env["OPENAI_API_KEYS"] == "sk-or-123"
    assert json.loads(env["OPENAI_API_CONFIGS"])["0"]["model_ids"] == ["anthropic/claude-haiku-4.5", "x/y"]
    terminal = json.loads(env["TERMINAL_SERVER_CONNECTIONS"])
    assert terminal == [{"id": "greenhouse-sandbox", "name": "Greenhouse sandbox", "url": "http://abc-greenhouse-sandbox:8000",
                         "key": "k" * 20, "auth_type": "bearer", "enabled": True}]
    for off in ("ENABLE_OLLAMA_API", "ENABLE_DIRECT_CONNECTIONS", "ENABLE_COMMUNITY_SHARING", "ENABLE_WEB_SEARCH",
                "ENABLE_IMAGE_GENERATION", "ENABLE_CODE_EXECUTION", "ENABLE_CODE_INTERPRETER"):
        assert env[off] == "false", off
    assert env["ENABLE_SIGNUP"] == "true" and env["DEFAULT_USER_ROLE"] == "pending" and env["ENABLE_AUTOMATIONS"] == "true"
    assert env["DATA_DIR"].startswith("/data/") and env["WEBUI_SECRET_KEY_FILE"].startswith("/data/")  # backed up, persistent
    assert webui_env.build_env({**GOOD_WEBUI, "allow_signup": False})["ENABLE_SIGNUP"] == "false"
    assert not [k for k in env if "HASS" in k or "SUPERVISOR" in k or "HOMEASSISTANT" in k]
    assert json.loads(webui_env.build_env(GOOD_WEBUI)["OPENAI_API_CONFIGS"])["0"]["model_ids"] == []


@pytest.mark.parametrize("change", [
    {"openrouter_api_key": ""}, {"terminal_host": ""}, {"terminal_host": "http://x"}, {"terminal_host": "x:8000"},
    {"terminal_api_key": "short"},
])
def test_webui_refuses_incomplete_options(change):
    with pytest.raises(ValueError):
        webui_env.build_env({**GOOD_WEBUI, **change})


NASTY = "ab'c\"$(echo pwned)`x y\\z;&|"


@pytest.mark.parametrize(("script", "options", "var"), [
    (SANDBOX, {**GOOD_SANDBOX, "api_key": NASTY * 2, "gateway_key": NASTY}, "OPEN_TERMINAL_API_KEY"),
    (SANDBOX, {**GOOD_SANDBOX, "api_key": NASTY * 2, "gateway_key": NASTY}, "GATEWAY_KEY"),
    (WEBUI, {**GOOD_WEBUI, "openrouter_api_key": NASTY, "terminal_api_key": NASTY * 2}, "OPENAI_API_KEYS"),
], ids=["sandbox-api-key", "sandbox-gateway-key", "webui-openrouter-key"])
def test_awkward_characters_in_keys_survive_the_shell_exactly(tmp_path, script, options, var):
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    run = subprocess.run(["sh", "-c", f'eval "$(python3 {script / "options_to_env.py"} {path})"; printf %s "${var}"'],
                         capture_output=True, text=True, check=False)
    expected = {"OPEN_TERMINAL_API_KEY": options.get("api_key"), "GATEWAY_KEY": options.get("gateway_key"),
                "OPENAI_API_KEYS": options.get("openrouter_api_key")}[var]
    assert run.stdout == expected and run.returncode == 0  # nothing was executed, nothing altered


@pytest.mark.parametrize(("script", "bad"), [(SANDBOX, {**GOOD_SANDBOX, "gateway_host": ""}),
                                             (WEBUI, {**GOOD_WEBUI, "openrouter_api_key": ""})])
def test_bad_options_stop_the_add_on_with_the_reason(tmp_path, script, bad):
    path = tmp_path / "options.json"
    path.write_text(json.dumps(bad))
    run = subprocess.run(["sh", "-c", f'eval "$(python3 {script / "options_to_env.py"} {path})"; echo STARTED'],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 1 and "STARTED" not in run.stdout and "is required" in run.stderr or "must be" in run.stderr


@pytest.mark.skipif(not Path("/home/user/open-webui/open-webui/backend/open_webui/config.py").exists(),
                    reason="Open WebUI source checkout not available")
def test_every_setting_we_send_really_exists_in_open_webui():
    checkout = Path("/home/user/open-webui/open-webui")
    # Some settings are read by Open WebUI's Python, others by its start script or its libraries (Dockerfile).
    source = "".join(p.read_text(errors="ignore") for p in [*(checkout / "backend").rglob("*.py"),
                                                             checkout / "backend/start.sh", checkout / "Dockerfile"])
    missing = [n for n in webui_env.build_env(GOOD_WEBUI) if not re.search(rf"\b{n}\b", source)]
    assert missing == []


# ---------------- the gateway client, against the real gateway -----------------

URL = "http://gateway:8099"


def via(client: TestClient):
    def transport(method, url, headers, body):
        response = client.request(method, url[len(URL):], headers=headers, content=body)
        return response.status_code, response.content
    return transport


@pytest.fixture
def gateway_app(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOG_DB", str(tmp_path / "c.sqlite3"))
    monkeypatch.setenv("GLOSSARY_DB", str(tmp_path / "g.sqlite3"))
    monkeypatch.setenv("GATEWAY_API_KEY", "gw-secret")
    ents = {"sensor.plug_a_power": EntityRecord("sensor.plug_a_power", friendly_name="Plug A Power", unit="W",
                                                device_class="power", vm_status="ok", area="Greenhouse")}
    series = [SeriesRecord("sensor.plug_a_power", "W_value", {"entity_id": "plug_a_power", "domain": "sensor"},
                           '{__name__="W_value",entity_id="plug_a_power"}', samples=100, field="value", kind="value")]
    conn = catalog_store.connect(tmp_path / "c.sqlite3")
    catalog_store.save_run(conn, "influxdb", ents, series)
    glossary = glossary_store.connect(str(tmp_path / "g.sqlite3"))
    glossary_store.sync(conn, glossary, now=1.0)
    return TestClient(app)


def test_client_reads_the_real_gateway(gateway_app):
    sdk = load(SDK, "gh_sdk")
    gw = sdk.Gateway(URL, "gw-secret", transport=via(gateway_app))
    found = gw.catalog_search("power")
    assert found[0]["entity_id"] == "sensor.plug_a_power" and found[0]["series"][0]["selector"].startswith("{__name__")
    assert gw.glossary(status="draft")[0]["entity_id"] == "sensor.plug_a_power"
    assert gw.catalog_status()["latest_run"]["n_entities"] == 1


def test_client_reports_refusals_with_the_gateways_reason(gateway_app):
    sdk = load(SDK, "gh_sdk")
    wrong_key = sdk.Gateway(URL, "nope", transport=via(gateway_app))
    with pytest.raises(sdk.GatewayError, match="401.*X-Gateway-Key"):
        wrong_key.catalog_status()
    gw = sdk.Gateway(URL, "gw-secret", transport=via(gateway_app))
    app.dependency_overrides[deps.get_ha] = lambda: object()  # the as-of refusal happens before Home Assistant is touched
    with pytest.raises(sdk.GatewayError, match="409.*as-of"):
        sdk.Gateway(URL, "gw-secret", as_of="2026-10-01T00:00:00Z", transport=via(gateway_app)).states()
    with pytest.raises(sdk.GatewayError, match="400"):
        gw.vm_query("up @ 1700000000")


def test_the_client_has_no_way_to_write_or_approve(gateway_app):
    sdk = load(SDK, "gh_sdk")
    methods = {m for m in dir(sdk.Gateway) if not m.startswith("_")}
    assert not methods & {"put", "delete", "post", "approve", "approve_glossary", "call_service", "turn_on", "set_state"}
    gw = sdk.Gateway(URL, "gw-secret", transport=via(gateway_app))
    draft = gw.propose_glossary("sensor.plug_a_power", "Power drawn by plug A, which runs the heater")
    assert draft["status"] == "draft" and draft["approved_by"] is None
    # even by hand, the API key cannot approve
    with pytest.raises(sdk.GatewayError, match="403.*owner only"):
        gw._call("PUT", "/glossary/sensor.plug_a_power", body={"status": "approved"})


def test_client_sends_the_as_of_time_and_caller_and_needs_a_url():
    sdk = load(SDK, "gh_sdk")
    seen = {}

    def spy(method, url, headers, body):
        seen.update(headers=headers, url=url)
        return 200, b"[]"

    sdk.Gateway(URL, "k", as_of="2026-10-01T00:00:00Z", caller="watch", transport=spy).glossary(q="a b")
    assert seen["headers"]["X-As-Of"] == "2026-10-01T00:00:00Z" and seen["headers"]["X-Caller"] == "watch"
    assert seen["url"] == f"{URL}/glossary?q=a+b"
    sdk.Gateway(URL, "k", transport=spy).glossary()
    assert "X-As-Of" not in seen["headers"]
    with pytest.raises(sdk.GatewayError, match="GATEWAY_URL"):
        sdk.Gateway(url="", key="k")


# ---------------- the chat tool -----------------

def block(path: Path) -> str:
    text = path.read_text()
    return text.split("---\n", 1)[1].split("# --- end client ---")[0]


def test_tool_embeds_exactly_the_sandbox_client():
    assert block(TOOL) == block(SDK)  # regenerate the tool from the sdk if this fails


@pytest.fixture
def tool(gateway_app):
    module = load(TOOL, "gh_tool")
    t = module.Tools()
    t.valves.GATEWAY_URL, t.valves.GATEWAY_KEY = URL, "gw-secret"
    t._gw = lambda: module.Gateway(URL, "gw-secret", as_of=t.valves.AS_OF or None, caller="open-webui",
                                   transport=via(gateway_app))
    return t


def test_tool_methods_are_described_for_the_model():
    module = load(TOOL, "gh_tool")
    for name in ("find_entities", "query_metric", "query_metric_range", "entity_history", "weather_forecast", "glossary",
                 "suggest_meaning", "automations", "where_is_entity_used", "read_config_file"):
        method = getattr(module.Tools, name)
        doc = method.__doc__ or ""
        params = [p for p in method.__code__.co_varnames[1:method.__code__.co_argcount]]
        assert doc.strip() and ":return:" in doc, name
        assert all(f":param {p}:" in doc for p in params), name  # Open WebUI builds the tool spec from these


def test_tool_finds_entities_and_answers_as_text(tool):
    answer = json.loads(tool.find_entities("power"))
    assert answer[0]["entity_id"] == "sensor.plug_a_power" and answer[0]["series"][0]["metric"] == "W_value"
    assert isinstance(tool.glossary("plug"), str) and "sensor.plug_a_power" in tool.glossary("plug")


def test_tool_turns_gateway_failures_into_text_not_exceptions(tool):
    tool.valves.AS_OF = "2026-10-01T00:00:00Z"
    reply = tool.read_config_file("configuration.yaml")
    assert reply.startswith("Gateway error:") and "snapshot" in reply
    assert tool.query_metric("up @ 1").startswith("Gateway error:") and "@" in tool.query_metric("up @ 1")


def test_tool_summarizes_series_instead_of_dumping_them():
    module = load(TOOL, "gh_tool")
    rows = [[1_800_000_000 + i * 3600, str(10 + i)] for i in range(100)]
    s = module.summarize_series({"metric": {"__name__": "W_value", "db": "homeassistant", "entity_id": "x"}, "values": rows})
    assert s["points"] == 100 and s["min"] == 10 and s["max"] == 109 and s["mean"] == 59.5
    assert s["first"] == ["2027-01-15T08:00:00Z", 10.0] and len(s["sample"]) <= 24 and "db" not in s["metric"]
    junk = module.summarize_series({"metric": {}, "values": [[1, "NaN"], [2, "+Inf"], [3, "5"]]})
    assert junk["points"] == 1 and junk["max"] == 5
    assert module.summarize_series({"metric": {}, "values": []}) == {"metric": {}, "points": 0}
    one = module.summarize_series({"metric": {}, "value": [1_800_000_000, "3.5"]})
    assert one["points"] == 1 and one["last"][1] == 3.5
    hist = module.summarize_history([[{"entity_id": "a.b", "state": "on", "last_changed": "t1"},
                                      {"entity_id": "a.b", "state": "off", "last_changed": "t2"}], []])
    assert hist == {"a.b": {"changes": 2, "first": ("t1", "on"), "last": ("t2", "off"), "sample": [("t1", "on"), ("t2", "off")]}}


def test_tool_truncates_long_answers_and_says_so(tool):
    tool.valves.MAX_CHARS = 600
    out = tool._out({"rows": ["x" * 50] * 100})
    assert len(out) < 800 and "truncated" in out


def test_replay_valve_reaches_the_gateway(gateway_app):
    module = load(TOOL, "gh_tool")
    seen = {}

    def spy(method, url, headers, body):
        seen.update(headers)
        return 200, b"[]"

    t = module.Tools()
    t.valves.GATEWAY_URL, t.valves.GATEWAY_KEY, t.valves.AS_OF = URL, "k", "2026-10-01T12:00:00Z"
    gw = t._gw()
    gw._transport = spy
    gw.glossary()
    assert seen["X-As-Of"] == "2026-10-01T12:00:00Z" and seen["X-Caller"] == "open-webui"
