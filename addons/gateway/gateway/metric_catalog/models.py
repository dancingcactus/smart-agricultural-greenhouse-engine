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
    vm_status: str = "missing"  # ok | missing | orphan


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
