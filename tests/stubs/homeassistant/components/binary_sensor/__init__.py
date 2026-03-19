"""Minimal stub for homeassistant.components.binary_sensor."""

from __future__ import annotations

from enum import StrEnum


class BinarySensorDeviceClass(StrEnum):
    BATTERY = "battery"
    BATTERY_CHARGING = "battery_charging"
    COLD = "cold"
    CONNECTIVITY = "connectivity"
    DOOR = "door"
    GAS = "gas"
    HEAT = "heat"
    LIGHT = "light"
    LOCK = "lock"
    MOISTURE = "moisture"
    MOTION = "motion"
    MOVING = "moving"
    OCCUPANCY = "occupancy"
    OPENING = "opening"
    PLUG = "plug"
    POWER = "power"
    PRESENCE = "presence"
    PROBLEM = "problem"
    RUNNING = "running"
    SAFETY = "safety"
    SMOKE = "smoke"
    SOUND = "sound"
    TAMPER = "tamper"
    UPDATE = "update"
    VIBRATION = "vibration"
    WINDOW = "window"


class BinarySensorEntity:
    _attr_is_on: bool | None = None
    _attr_device_class: BinarySensorDeviceClass | None = None

    @property
    def is_on(self) -> bool | None:
        return self._attr_is_on

    def async_write_ha_state(self) -> None:
        pass
