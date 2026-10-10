"""Copy the gateway client from the sandbox library into the Open WebUI tool.

Open WebUI tools are single pasted files and cannot import from this repository, so the tool embeds
the client. Run this after editing addons/open-terminal/sdk/greenhouse_gateway.py; a test fails if
the two copies differ.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "addons/open-terminal/sdk/greenhouse_gateway.py"
TOOL = ROOT / "owui/tools/greenhouse_gateway.py"
START = re.compile(r"# --- client \([^\n]*\) ---\n")
END = "# --- end client ---"


def main() -> int:
    sdk, tool = SDK.read_text(), TOOL.read_text()
    code = sdk[START.search(sdk).end(): sdk.index(END)]
    match = START.search(tool)
    updated = tool[: match.end()] + code + tool[tool.index(END):]
    if updated != tool:
        TOOL.write_text(updated)
        print(f"updated {TOOL.relative_to(ROOT)}")
    else:
        print("already in sync")
    return 0


if __name__ == "__main__":
    sys.exit(main())
