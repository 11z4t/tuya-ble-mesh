"""Button entities for Tuya BLE Mesh devices.

Provides Identify and Reconnect as discoverable UI buttons —
no need to use Developer Tools → Services.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.tuya_ble_mesh.entity import TuyaBLEMeshEntity

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from custom_components.tuya_ble_mesh import TuyaBLEMeshConfigEntry
    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

    AddEntitiesCallback = Callable[..., None]

_LOGGER = logging.getLogger(__name__)

_IDENTIFY_FLASH_INTERVAL = 0.5  # seconds between on/off flashes
_IDENTIFY_FLASH_COUNT = 3


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TuyaBLEMeshConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE Mesh button entities."""
    runtime_data = entry.runtime_data
    coordinator: TuyaBLEMeshCoordinator = runtime_data.coordinator
    device_info: DeviceInfo = runtime_data.device_info

    async_add_entities(
        [
            TuyaBLEMeshIdentifyButton(coordinator, entry.entry_id, device_info),
            TuyaBLEMeshReconnectButton(coordinator, entry.entry_id, device_info),
        ]
    )


class TuyaBLEMeshIdentifyButton(TuyaBLEMeshEntity, ButtonEntity):
    """Button that flashes the device LED to help locate it."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:flash-alert"
    _attr_translation_key = "identify"

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        super().__init__(coordinator, entry_id, device_info)
        self._attr_unique_id = f"{coordinator.device.address}_identify"

    async def async_press(self) -> None:
        """Flash device LED for identification."""
        device = self.coordinator.device
        if not hasattr(device, "send_power"):
            _LOGGER.warning("Device %s does not support identify", device.address)
            return
        for _ in range(_IDENTIFY_FLASH_COUNT):
            await device.send_power(False)
            await asyncio.sleep(_IDENTIFY_FLASH_INTERVAL)
            await device.send_power(True)
            await asyncio.sleep(_IDENTIFY_FLASH_INTERVAL)


class TuyaBLEMeshReconnectButton(TuyaBLEMeshEntity, ButtonEntity):
    """Button to force disconnect and reconnect the device."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "reconnect"

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        super().__init__(coordinator, entry_id, device_info)
        self._attr_unique_id = f"{coordinator.device.address}_reconnect"

    @property
    def available(self) -> bool:
        """Always available — reconnect works even when device is offline."""
        return True

    async def async_press(self) -> None:
        """Force disconnect and reconnect."""
        with contextlib.suppress(OSError, ConnectionError):
            await self.coordinator.device.disconnect()
        self.coordinator.schedule_reconnect()
