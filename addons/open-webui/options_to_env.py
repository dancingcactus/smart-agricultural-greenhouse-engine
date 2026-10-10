"""Turn the add-on's options into the environment Open WebUI reads.

Prints `export NAME='value'` lines for the shell to eval; exits non-zero with a message when the
options are incomplete. Everything the add-on controls is set here and persistent config is turned
off, so a restart always re-applies these options instead of whatever the admin screens last held.
"""

import json
import shlex
import sys

OPENROUTER_URL = "https://openrouter.ai/api/v1"
TERMINAL_ID = "greenhouse-sandbox"
MIN_KEY_LENGTH = 16


def build_env(options: dict) -> dict:
    router_key = (options.get("openrouter_api_key") or "").strip()
    if not router_key:
        raise ValueError("openrouter_api_key is required: create one at openrouter.ai and set a credit limit on it")
    host = (options.get("terminal_host") or "").strip()
    if not host or any(c in host for c in "/: "):
        raise ValueError("terminal_host must be the SAGE Sandbox add-on's bare hostname (no http://, port or path)")
    terminal_key = (options.get("terminal_api_key") or "").strip()
    if len(terminal_key) < MIN_KEY_LENGTH:
        raise ValueError(f"terminal_api_key must be at least {MIN_KEY_LENGTH} characters and match the sandbox's api_key")
    models = [m.strip() for m in options.get("allowed_models") or [] if m and m.strip()]
    port = int(options.get("terminal_port") or 8000)
    return {
        "PORT": "8080",
        "DATA_DIR": "/data/webui",
        "WEBUI_SECRET_KEY_FILE": "/data/.webui_secret_key",  # survives rebuilds, so logins stay valid
        "WEBUI_NAME": "SAGE",
        # The options above are the source of truth: re-apply them on every start.
        "ENABLE_PERSISTENT_CONFIG": "false",
        "WEBUI_AUTH": "true",
        "ENABLE_SIGNUP": "true" if options.get("allow_signup", True) else "false",
        "DEFAULT_USER_ROLE": "pending",  # new accounts wait for an admin
        # Models come only from OpenRouter.
        "ENABLE_OLLAMA_API": "false",
        "ENABLE_OPENAI_API": "true",
        "OPENAI_API_BASE_URLS": OPENROUTER_URL,
        "OPENAI_API_KEYS": router_key,
        # An empty model_ids list means every model OpenRouter offers.
        "OPENAI_API_CONFIGS": json.dumps({"0": {"enable": True, "model_ids": models, "connection_type": "external",
                                                "prefix_id": ""}}),
        # The agent's shell is the sandbox only, reached through Open WebUI's own backend.
        "TERMINAL_SERVER_CONNECTIONS": json.dumps([{
            "id": TERMINAL_ID, "name": "Greenhouse sandbox", "url": f"http://{host}:{int(port)}",
            "key": terminal_key, "auth_type": "bearer", "enabled": True}]),
        "ENABLE_AUTOMATIONS": "true",
        # No other ways out and no extras: users cannot add their own endpoints, and nothing is shared.
        "ENABLE_DIRECT_CONNECTIONS": "false",
        "ENABLE_COMMUNITY_SHARING": "false",
        "ENABLE_WEB_SEARCH": "false",
        "ENABLE_IMAGE_GENERATION": "false",
        "ENABLE_CODE_EXECUTION": "false",
        "ENABLE_CODE_INTERPRETER": "false",
        "SCARF_NO_ANALYTICS": "true",
        "DO_NOT_TRACK": "true",
        "ANONYMIZED_TELEMETRY": "false",
    }


def main(path: str) -> int:
    try:
        with open(path) as fh:
            env = build_env(json.load(fh))
    except (OSError, ValueError) as exc:
        print(f"echo {shlex.quote('Greenhouse Open WebUI: ' + str(exc))} >&2; exit 1")
        return 1
    for name, value in env.items():
        print(f"export {name}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/data/options.json"))
