"""Minimal stub for homeassistant.helpers.entity."""
from __future__ import annotations
from enum import StrEnum
from typing import Any


class EntityCategory(StrEnum):
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


class Entity:
    entity_id: str = ""
    _attr_name: str | None = None
    _attr_unique_id: str | None = None
    _attr_available: bool = True
    _attr_should_poll: bool = False
    _attr_device_class: Any = None
    _attr_has_entity_name: bool = False
    _attr_entity_category: Any = None
    _attr_device_info: Any = None

    @property
    def name(self) -> str | None:
        return self._attr_name

    @property
    def unique_id(self) -> str | None:
        return self._attr_unique_id

    @property
    def available(self) -> bool:
        return self._attr_available

    @property
    def should_poll(self) -> bool:
        return self._attr_should_poll

    @property
    def device_class(self) -> Any:
        desc = getattr(self, "entity_description", None)
        if desc is not None and getattr(desc, "device_class", None) is not None:
            return desc.device_class
        return self._attr_device_class

    @property
    def has_entity_name(self) -> bool:
        return self._attr_has_entity_name

    @property
    def entity_category(self) -> Any:
        desc = getattr(self, "entity_description", None)
        if desc is not None and getattr(desc, "entity_category", None) is not None:
            return desc.entity_category
        return self._attr_entity_category

    async def async_update(self) -> None:
        pass

    async def async_added_to_hass(self) -> None:
        pass

    async def async_will_remove_from_hass(self) -> None:
        pass

    def async_write_ha_state(self) -> None:
        pass

    def schedule_update_ha_state(self, force_refresh: bool = False) -> None:
        pass
