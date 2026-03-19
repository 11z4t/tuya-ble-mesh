"""Minimal stub for homeassistant.components.update."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class UpdateDeviceClass(StrEnum):
    FIRMWARE = "firmware"


class UpdateEntity:
    _attr_installed_version: str | None = None
    _attr_latest_version: str | None = None
    _attr_device_class: UpdateDeviceClass | None = None

    @property
    def installed_version(self) -> str | None:
        return self._attr_installed_version

    @property
    def latest_version(self) -> str | None:
        return self._attr_latest_version

    @property
    def device_class(self) -> UpdateDeviceClass | None:
        return self._attr_device_class

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        pass
