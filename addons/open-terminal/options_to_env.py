"""Turn the add-on's options into the environment Open Terminal and the gateway client read.

Prints `export NAME='value'` lines for the shell to eval; exits non-zero with a message if the
options are unsafe or incomplete, so the add-on stops instead of starting without its firewall.
"""

import json
import shlex
import sys

MIN_KEY_LENGTH = 16


def build_env(options: dict) -> dict:
    host = (options.get("gateway_host") or "").strip()
    if not host:
        raise ValueError("gateway_host is required: the Greenhouse Gateway add-on's hostname. The sandbox may "
                         "reach nothing else, so starting without it would leave the sandbox with no data.")
    if "/" in host or ":" in host or " " in host:
        raise ValueError("gateway_host must be a bare hostname, such as abc123_greenhouse_gateway (no http://, port or path)")
    for name in ("api_key", "gateway_key"):
        if len((options.get(name) or "").strip()) < (MIN_KEY_LENGTH if name == "api_key" else 1):
            raise ValueError(f"{name} is required" + (f" (at least {MIN_KEY_LENGTH} characters)" if name == "api_key" else ""))
    extras = [d.strip() for d in options.get("extra_allowed_domains") or [] if d and d.strip()]
    if any("," in d or " " in d or "/" in d for d in extras):
        raise ValueError("extra_allowed_domains entries must be bare domain names")
    port = int(options.get("gateway_port") or 8099)
    return {
        "OPEN_TERMINAL_API_KEY": options["api_key"].strip(),
        # Anything not listed here is unreachable from inside the sandbox (DNS and iptables enforce it).
        "OPEN_TERMINAL_ALLOWED_DOMAINS": ",".join([host, *extras]),
        "GATEWAY_URL": f"http://{host}:{port}",
        "GATEWAY_KEY": options["gateway_key"].strip(),
    }


def main(path: str) -> int:
    try:
        with open(path) as fh:
            env = build_env(json.load(fh))
    except (OSError, ValueError) as exc:
        print(f"echo {shlex.quote('Greenhouse sandbox: ' + str(exc))} >&2; exit 1")
        return 1
    for name, value in env.items():
        print(f"export {name}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "/data/options.json"))
