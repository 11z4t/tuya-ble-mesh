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


class TuyaBLEMeshSwitch(SwitchEntity):  # type: ignore[misc]
    """Switch entity for a Tuya BLE Mesh smart plug."""

    _attr_should_poll = False
    _attr_device_class = SwitchDeviceClass.OUTLET
    _attr_has_entity_name = True
    _attr_name = None  # Use device name as entity name

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator.device.address}_switch"
        if device_info is not None:
            self._attr_device_info = device_info
        self._remove_listener: Any = None

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._attr_unique_id

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._coordinator.state.available

    @property
    def is_on(self) -> bool:
        """Return True if the switch is on."""
        return self._coordinator.state.is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on.

        Args:
            **kwargs: Additional arguments (unused).
        """
        await self._coordinator.device.send_power(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off.

        Args:
            **kwargs: Additional arguments (unused).
        """
        await self._coordinator.device.send_power(False)

    async def async_added_to_hass(self) -> None:
        """Register state listener when added to HA."""
        self._remove_listener = self._coordinator.add_listener(self._handle_coordinator_update)

    async def async_will_remove_from_hass(self) -> None:
        """Remove state listener when removed from HA."""
        if self._remove_listener is not None:
            self._remove_listener()
            self._remove_listener = None

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self.async_write_ha_state()
