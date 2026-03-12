"""Switch entity platform for Tuya BLE Mesh smart plugs."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.helpers.device_registry import DeviceInfo

from custom_components.tuya_ble_mesh.const import (
    CONF_DEVICE_TYPE,
    PLUG_DEVICE_TYPES,
)
from custom_components.tuya_ble_mesh.entity import TuyaBLEMeshEntity

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant

    from custom_components.tuya_ble_mesh import TuyaBLEMeshConfigEntry
    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

    AddEntitiesCallback = Callable[..., None]

_LOGGER = logging.getLogger(__name__)

# BLE mesh serializes commands — limit to one concurrent update
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TuyaBLEMeshConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE Mesh switch entities from a config entry.

    Args:
        hass: Home Assistant instance.
        entry: Config entry being set up.
        async_add_entities: Callback to register new entities.
    """
    if entry.data.get(CONF_DEVICE_TYPE) not in PLUG_DEVICE_TYPES:
        return
    runtime_data = entry.runtime_data
    coordinator: TuyaBLEMeshCoordinator = runtime_data.coordinator
    device_info: DeviceInfo = runtime_data.device_info
    async_add_entities([TuyaBLEMeshSwitch(coordinator, entry.entry_id, device_info)])


class TuyaBLEMeshSwitch(TuyaBLEMeshEntity, SwitchEntity):
    """Switch entity for a Tuya BLE Mesh smart plug."""

    _attr_should_poll = False
    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_name = None  # Use device name as entity name
    _attr_unique_id: str

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        super().__init__(coordinator, entry_id, device_info)
        self._attr_unique_id = f"{coordinator.device.address}_switch"

    @property
    def is_on(self) -> bool:
        """Return True if the switch is on."""
        return self.coordinator.state.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on.

        Args:
            **kwargs: Additional arguments (unused).
        """
        await self.coordinator.send_command_with_retry(
            lambda: self.coordinator.device.send_power(True),  # type: ignore[arg-type]
            description="send_power(True)",
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off.

        Args:
            **kwargs: Additional arguments (unused).
        """
        await self.coordinator.send_command_with_retry(
            lambda: self.coordinator.device.send_power(False),  # type: ignore[arg-type]
            description="send_power(False)",
        )
