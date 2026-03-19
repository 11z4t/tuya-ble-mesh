"""Minimal stub for homeassistant.components.button."""

from __future__ import annotations

from enum import StrEnum


class ButtonDeviceClass(StrEnum):
    IDENTIFY = "identify"
    RESTART = "restart"
    UPDATE = "update"


class ButtonEntity:
    _attr_device_class: ButtonDeviceClass | None = None

    async def async_press(self) -> None:
        pass

    def async_write_ha_state(self) -> None:
        pass
