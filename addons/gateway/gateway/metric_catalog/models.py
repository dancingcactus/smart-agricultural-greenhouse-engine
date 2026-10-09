from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EntityRecord:
    entity_id: str
    name: str = ""
    friendly_name: str = ""
    labels: list[str] = field(default_factory=list)
    icon: str = ""
    platform: str = ""
    device_id: str = ""
    area: str = ""
    unit: str = ""
    device_class: str = ""
    state_class: str = ""
    disabled: bool = False
    in_registry: bool = False
    has_state: bool = False
    mirror_of: str = ""  # for a snapshot sensor: the helper it copies
    mirrored_by: str = ""  # for a helper: the snapshot sensor that records it
    vm_status: str = "missing"  # ok | string_only | mirrored | missing | orphan | ignored
    vm_reason: str = ""  # for missing: disabled | no_state | non_numeric | not_exported


@dataclass
class SeriesRecord:
    entity_id: str
    metric: str
    labels: dict[str, str]
    selector: str
    first_seen: float | None = None
    last_seen: float | None = None
    samples: int | None = None
    avg_interval_s: float | None = None
    field: str = ""  # InfluxDB field name, e.g. value, device_class_str
    kind: str = ""  # value | attribute_num | attribute_str | state
    history_first_seen: float | None = None  # earliest sample over the long history window
