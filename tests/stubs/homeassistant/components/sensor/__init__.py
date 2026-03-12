"""Minimal stub for homeassistant.components.sensor."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class SensorDeviceClass(StrEnum):
    BATTERY = "battery"
    CURRENT = "current"
    ENERGY = "energy"
    ENUM = "enum"
    ILLUMINANCE = "illuminance"
    POWER = "power"
    SIGNAL_STRENGTH = "signal_strength"
    TEMPERATURE = "temperature"
    TIMESTAMP = "timestamp"
    VOLTAGE = "voltage"


class SensorStateClass(StrEnum):
    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


@dataclass(frozen=True, kw_only=True)
class SensorEntityDescription:
    key: str = ""
    name: str | None = None
    translation_key: str | None = None
    device_class: SensorDeviceClass | str | None = None
    state_class: SensorStateClass | str | None = None
    native_unit_of_measurement: str | None = None
    entity_category: Any = None
    icon: str | None = None
    entity_registry_enabled_default: bool = True
    suggested_display_precision: int | None = None
    has_entity_name: bool = False
    options: list[str] | None = None


class SensorEntity:
    _attr_native_value: Any = None
    _attr_device_class: SensorDeviceClass | None = None
    _attr_state_class: SensorStateClass | None = None
    _attr_native_unit_of_measurement: str | None = None

    @property
    def native_value(self) -> Any:
        return self._attr_native_value

    @property
    def device_class(self) -> SensorDeviceClass | str | None:
        desc = getattr(self, "entity_description", None)
        if desc is not None and getattr(desc, "device_class", None) is not None:
            return desc.device_class
        return self._attr_device_class

    @property
    def state_class(self) -> SensorStateClass | str | None:
        desc = getattr(self, "entity_description", None)
        if desc is not None and getattr(desc, "state_class", None) is not None:
            return desc.state_class
        return self._attr_state_class

    def async_write_ha_state(self) -> None:
        pass
