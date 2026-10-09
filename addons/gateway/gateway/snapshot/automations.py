"""Catalog of the automations in the mirrored config: what each does and which entities it touches."""

from __future__ import annotations

import json
import re
from fnmatch import fnmatchcase
from pathlib import Path

import yaml

ENTITY_TOKEN = re.compile(r"\b[a-z][a-z0-9_]*\.[a-z0-9][a-z0-9_]*\b")
MANAGED_LABEL = "agent-managed"


class _Loader(yaml.SafeLoader):
    """Reads Home Assistant YAML, keeping !secret / !include / !input tags as plain text."""


def _tagged(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return f"!{suffix} {loader.construct_scalar(node)}"
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_mapping(node, deep=True)


_Loader.add_multi_constructor("!", _tagged)


def _load(path: Path):
    return yaml.load(path.read_text(errors="replace"), Loader=_Loader)


def _looks_like_automation(item) -> bool:
    return isinstance(item, dict) and any(k in item for k in ("trigger", "triggers", "action", "actions", "alias"))


def _as_automations(value) -> list[dict]:
    if isinstance(value, list):
        return [v for v in value if _looks_like_automation(v)]
    if isinstance(value, dict):
        if _looks_like_automation(value):
            return [value]
        return [{"id": k, **v} if "id" not in v else v for k, v in value.items() if _looks_like_automation(v)]
    return []


def find_automations(root: Path) -> tuple[list[tuple[str, dict]], list[dict]]:
    """Automations from automations.yaml, automations/, packages/ and configuration.yaml; plus files not parsed."""
    found: list[tuple[str, dict]] = []
    problems: list[dict] = []
    candidates = [root / "automations.yaml", root / "configuration.yaml"]
    candidates += sorted((root / "automations").rglob("*.yaml")) if (root / "automations").is_dir() else []
    candidates += sorted((root / "packages").rglob("*.yaml")) if (root / "packages").is_dir() else []
    for path in candidates:
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            data = _load(path)
        except yaml.YAMLError as exc:
            problems.append({"path": rel, "problem": f"YAML error: {str(exc).splitlines()[0]}"})
            continue
        if rel in ("configuration.yaml",) or rel.startswith("packages/"):
            data = (data or {}).get("automation") if isinstance(data, dict) else None
        if isinstance(data, str) and data.startswith("!include"):
            continue  # points at a file we parse separately
        found += [(rel, a) for a in _as_automations(data)]
    return found, problems


def _walk(value, key_names: set[str], out: list[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            if k in key_names and isinstance(v, str) and "." in v and " " not in v:
                out.append(v)
            _walk(v, key_names, out)
    elif isinstance(value, list):
        for v in value:
            _walk(v, key_names, out)


def _one(value, *keys):
    return next((value[k] for k in keys if k in value), None)


def summarize(auto: dict, known_entities: set[str]) -> dict:
    text = json.dumps(auto, default=str)
    services: list[str] = []
    _walk(_one(auto, "actions", "action") or [], {"action", "service"}, services)
    triggers = []
    for trig in _as_list(_one(auto, "triggers", "trigger")):
        if isinstance(trig, dict):
            kind = trig.get("trigger") or trig.get("platform")
            detail = {k: trig[k] for k in ("to", "from", "at", "event_type", "for", "above", "below") if k in trig}
            triggers.append({"type": kind, "entities": sorted(set(ENTITY_TOKEN.findall(json.dumps(trig, default=str)))
                                                              & known_entities), **detail})
    return {
        "config_id": str(auto["id"]) if auto.get("id") is not None else None,
        "alias": auto.get("alias"),
        "description": auto.get("description") or "",
        "mode": auto.get("mode", "single"),
        "triggers": triggers,
        "condition_count": len(_as_list(_one(auto, "conditions", "condition"))),
        "services": sorted(set(services)),
        "entities": sorted(set(ENTITY_TOKEN.findall(text)) & known_entities),
    }


def _as_list(value) -> list:
    return value if isinstance(value, list) else ([] if value is None else [value])


def build(root: Path, states: list[dict], registry: list[dict], label_names: dict[str, str],
          protected: list[str]) -> dict:
    """Return {automations: [...], entity_references: {entity: [automation ids]}, unparsed: [...]}."""
    known = {s["entity_id"] for s in states} | {r["entity_id"] for r in registry}
    state_by_id = {s["attributes"]["id"]: s for s in states
                   if s["entity_id"].startswith("automation.") and s.get("attributes", {}).get("id") is not None}
    state_by_alias = {s["attributes"].get("friendly_name"): s for s in states if s["entity_id"].startswith("automation.")}
    reg_labels = {r["entity_id"]: [label_names.get(x, x) for x in r.get("labels") or []] for r in registry}

    found, unparsed = find_automations(root)
    records = []
    for source, auto in found:
        rec = summarize(auto, known)
        state = state_by_id.get(rec["config_id"]) or state_by_alias.get(rec["alias"])
        eid = state["entity_id"] if state else None
        labels = sorted(reg_labels.get(eid, [])) if eid else []
        is_protected = any(fnmatchcase(x, pat) for pat in protected if pat.strip()
                           for x in filter(None, [eid, (rec["alias"] or "").lower(), rec["config_id"]]))
        records.append({
            **rec, "entity_id": eid, "source_file": source, "enabled": (state or {}).get("state") == "on" if state else None,
            "last_triggered": ((state or {}).get("attributes") or {}).get("last_triggered"),
            "labels": labels, "protected": is_protected,
            "managed": MANAGED_LABEL in labels and not is_protected,  # a protected automation is never managed
            "in_runtime": state is not None,
        })
    refs: dict[str, list[str]] = {}
    for rec in records:
        ident = rec["entity_id"] or f"config:{rec['config_id'] or rec['alias']}"
        for entity in rec["entities"]:
            refs.setdefault(entity, []).append(ident)
    return {"automations": records, "entity_references": {k: sorted(v) for k, v in sorted(refs.items())},
            "unparsed": unparsed}
