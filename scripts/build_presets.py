#!/usr/bin/env python3
"""Build Open WebUI model presets (owui/presets/sage-presets.json).

Each preset's system prompt = base prompt + role prompt + the greenhouse
context files, so the agent needs no file access. A short hash of the final
prompt is put in the description so runs can be tied to a prompt version.
Import the JSON in Open WebUI: Workspace -> Models -> Import.
"""
import hashlib
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CTX = ROOT / "context"


def build() -> list[dict]:
    cfg = yaml.safe_load((ROOT / "owui/presets/models.yaml").read_text())
    base = (CTX / "prompts/base.md").read_text().strip()
    context = "\n\n".join(
        f"## {name}\n{(CTX / name).read_text().strip()}"
        for name in ("greenhouse.md", "targets.yaml", "basket_map.yaml")
    )
    out = []
    for role, r in cfg["roles"].items():
        role_prompt = (CTX / f"prompts/{role}.md").read_text().strip()
        system = f"{base}\n\n{role_prompt}\n\n# Greenhouse context\n\n{context}\n"
        digest = hashlib.sha256(system.encode()).hexdigest()[:10]
        out.append({
            "id": f"sage-{role}",
            "name": r["name"],
            "base_model_id": r["model"],
            "is_active": True,
            "params": {
                "system": system,
                "temperature": r["temperature"],
                "function_calling": "native",
                # OpenRouter provider routing; verify it reaches the request.
                "provider": {"data_collection": "deny"},
            },
            "meta": {
                "description": f"SAGE {role} role. prompt {digest}",
                "toolIds": cfg["tool_ids"],
                "filterIds": cfg.get("filter_ids", []),
                "capabilities": {"citations": False},
            },
        })
    return out


if __name__ == "__main__":
    path = ROOT / "owui/presets/sage-presets.json"
    path.write_text(json.dumps(build(), indent=2) + "\n")
    print(f"wrote {path}")
