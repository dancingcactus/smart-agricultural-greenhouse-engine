"""Home Assistant side: one EntityRecord per entity, from registry plus live states."""

from __future__ import annotations

from gateway.ha_client import HAClient

from .models import EntityRecord


def collect_entities(ha: HAClient) -> dict[str, EntityRecord]:
    label_names = {
        lab["label_id"]: lab.get("name", lab["label_id"]) for lab in ha.ws_list("config/label_registry/list")
    }
    area_names = {a["area_id"]: a.get("name", a["area_id"]) for a in ha.ws_list("config/area_registry/list")}
    devices = {d["id"]: d for d in ha.ws_list("config/device_registry/list")}

    entities: dict[str, EntityRecord] = {}
    for reg in ha.ws_list("config/entity_registry/list"):
        eid = reg["entity_id"]
        area_id = reg.get("area_id") or devices.get(reg.get("device_id") or "", {}).get("area_id")
        entities[eid] = EntityRecord(
            entity_id=eid,
            name=reg.get("name") or reg.get("original_name") or "",
            labels=sorted(label_names.get(lid, lid) for lid in reg.get("labels") or []),
            icon=reg.get("icon") or reg.get("original_icon") or "",
            platform=reg.get("platform") or "",
            device_id=reg.get("device_id") or "",
            area=area_names.get(area_id, "") if area_id else "",
            device_class=reg.get("device_class") or reg.get("original_device_class") or "",
            disabled=bool(reg.get("disabled_by")),
            in_registry=True,
        )

    for st in ha.states():
        attrs = st.get("attributes", {})
        rec = entities.setdefault(st["entity_id"], EntityRecord(entity_id=st["entity_id"]))
        rec.has_state = True
        rec.friendly_name = attrs.get("friendly_name", "")
        rec.unit = attrs.get("unit_of_measurement", "") or ""
        rec.state_class = attrs.get("state_class", "") or ""
        rec.device_class = rec.device_class or attrs.get("device_class", "") or ""
        rec.icon = rec.icon or attrs.get("icon", "") or ""
    return entities
