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
    from custom_components.tuya_ble_mesh.entry_nodes import (
        add_node_entities,
        iter_entry_nodes,
        network_runtime,
    )

    for node in iter_entry_nodes(entry):
        coordinator: TuyaBLEMeshCoordinator = node.runtime.coordinator
        device_info: DeviceInfo = node.runtime.device_info
        add_node_entities(
            async_add_entities,
            node,
            [
                TuyaBLEMeshIdentifyButton(coordinator, node.entity_key, device_info),
                TuyaBLEMeshReconnectButton(coordinator, node.entity_key, device_info),
            ],
        )

    net = network_runtime(entry)
    if net is not None:
        from custom_components.tuya_ble_mesh.network_entities import MeshSwitchProxyButton

        async_add_entities([MeshSwitchProxyButton(net.manager, net.hub_device_info)])


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
        was_on = bool(self.coordinator.state.is_on)
        for _ in range(_IDENTIFY_FLASH_COUNT):
            await device.send_power(not was_on)
            await asyncio.sleep(_IDENTIFY_FLASH_INTERVAL)
            await device.send_power(was_on)
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
