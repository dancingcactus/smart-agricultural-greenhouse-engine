#!/usr/bin/env python3
"""Enter role aliases into the gateway glossary as drafts, from a roles.yaml file.

roles.yaml maps a role name to an entity id (or a list of them):

    greenhouse_temperature: sensor.temp5
    equipment_fans: [switch.north_fan, switch.south_fan]

Each entity gets the alias role:<name> and, if it has no meaning yet, a draft meaning. Nothing is approved:
open the SAGE panel in Home Assistant and approve the drafts. Entries an owner already approved are skipped.

    GATEWAY_URL=http://<host>:8099 GATEWAY_KEY=... python3 scripts/seed_roles.py roles.yaml [--dry-run]
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml


def call(base: str, key: str, method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        base.rstrip("/") + path, method=method, data=json.dumps(body).encode() if body else None,
        headers={"X-Gateway-Key": key, "X-Caller": "seed-roles", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def plan(roles: dict) -> dict[str, list[str]]:
    """entity id -> the role names it should carry."""
    out: dict[str, list[str]] = {}
    for role, entities in roles.items():
        for eid in [entities] if isinstance(entities, str) else entities:
            out.setdefault(eid, []).append(role)
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roles_file", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    base, key = os.environ.get("GATEWAY_URL", ""), os.environ.get("GATEWAY_KEY", "")
    if not base and not args.dry_run:
        print("set GATEWAY_URL (and GATEWAY_KEY)", file=sys.stderr)
        return 2
    wanted = plan(yaml.safe_load(args.roles_file.read_text()))
    failed = 0
    for eid, names in sorted(wanted.items()):
        aliases_new = [f"role:{n}" for n in names]
        if args.dry_run:
            print(f"would draft {eid}: {aliases_new}")
            continue
        try:
            entry = call(base, key, "GET", f"/glossary/{eid}")
            if entry["status"] == "approved":
                print(f"skip   {eid}: already approved (add the aliases in the panel if missing)")
                continue
            aliases = list(dict.fromkeys([*entry["aliases"], *aliases_new]))
            meaning = entry["meaning"] or f"Used as: {', '.join(names)}."
            call(base, key, "POST", "/glossary/drafts", {"entity_id": eid, "meaning": meaning, "aliases": aliases})
            print(f"drafted {eid}: {aliases_new}")
        except urllib.error.HTTPError as exc:
            failed += 1
            print(f"FAILED {eid}: {exc.code} {exc.read().decode()[:160]}", file=sys.stderr)
        except OSError as exc:
            print(f"cannot reach the gateway: {exc}", file=sys.stderr)
            return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
