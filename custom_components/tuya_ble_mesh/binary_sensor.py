"""Binary sensor entities for Tuya BLE Mesh devices.

Provides a connectivity binary sensor that tracks BLE connection state.
Unlike the `available` property (which hides the entity), this sensor
is always visible and lets users trigger automations on connect/disconnect.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory  # type: ignore[attr-defined]

from custom_components.tuya_ble_mesh.entity import TuyaBLEMeshEntity

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from custom_components.tuya_ble_mesh import TuyaBLEMeshConfigEntry
    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

    AddEntitiesCallback = Callable[..., None]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TuyaBLEMeshConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE Mesh binary sensor entities."""
    runtime_data = entry.runtime_data
    coordinator: TuyaBLEMeshCoordinator = runtime_data.coordinator
    device_info: DeviceInfo = runtime_data.device_info

    async_add_entities([
        TuyaBLEMeshConnectivitySensor(coordinator, entry.entry_id, device_info),
    ])


class TuyaBLEMeshConnectivitySensor(TuyaBLEMeshEntity, BinarySensorEntity):
    """Binary sensor indicating BLE mesh connection state.

    Always visible in the UI (unlike ``available`` which hides the entity).
    Enables automations like "when device disconnects → send notification".
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connectivity"

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        super().__init__(coordinator, entry_id, device_info)
        self._attr_unique_id = f"{coordinator.device.address}_connectivity"

    @property
    def available(self) -> bool:
        """Always available — shows disconnected state instead of hiding."""
        return True

    @property
    def is_on(self) -> bool:
        """Return True when BLE connection is active."""
        return self.coordinator.state.available
