"""Minimal stub for homeassistant.components.switch."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class SwitchDeviceClass(StrEnum):
    OUTLET = "outlet"
    SWITCH = "switch"


class SwitchEntity:
    _attr_is_on: bool | None = None
    _attr_device_class: SwitchDeviceClass | None = None

    @property
    def is_on(self) -> bool | None:
        return self._attr_is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        pass

    async def async_turn_off(self, **kwargs: Any) -> None:
        pass
