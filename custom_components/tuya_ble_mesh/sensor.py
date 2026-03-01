"""Sensor entities for Tuya BLE Mesh devices.

Provides RSSI (signal strength) and firmware version sensors.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

_LOGGER = logging.getLogger(__name__)


class TuyaBLEMeshRSSISensor:
    """RSSI signal strength sensor for a Tuya BLE Mesh device.

    Duck-typed to match HA's SensorEntity interface.
    """

    def __init__(self, coordinator: TuyaBLEMeshCoordinator, entry_id: str) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator.device.address}_rssi"
        self._attr_name = f"Tuya BLE Mesh {coordinator.device.address[-8:]} RSSI"
        self._remove_listener: Any = None

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._attr_unique_id

    @property
    def name(self) -> str:
        """Return entity name."""
        return self._attr_name

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._coordinator.state.available

    @property
    def native_value(self) -> int | None:
        """Return the RSSI value in dBm."""
        return self._coordinator.state.rssi

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "dBm"

    @property
    def device_class(self) -> str:
        """Return the device class."""
        return "signal_strength"

    @property
    def entity_category(self) -> str:
        """Return the entity category."""
        return "diagnostic"

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
        _LOGGER.debug("RSSI sensor updated: %s", self.native_value)


class TuyaBLEMeshFirmwareSensor:
    """Firmware version sensor for a Tuya BLE Mesh device.

    Duck-typed to match HA's SensorEntity interface.
    """

    def __init__(self, coordinator: TuyaBLEMeshCoordinator, entry_id: str) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator.device.address}_firmware"
        self._attr_name = f"Tuya BLE Mesh {coordinator.device.address[-8:]} Firmware"
        self._remove_listener: Any = None

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return self._attr_unique_id

    @property
    def name(self) -> str:
        """Return entity name."""
        return self._attr_name

    @property
    def available(self) -> bool:
        """Return True if the device is available."""
        return self._coordinator.state.available

    @property
    def native_value(self) -> str | None:
        """Return the firmware version."""
        return self._coordinator.state.firmware_version

    @property
    def entity_category(self) -> str:
        """Return the entity category."""
        return "diagnostic"

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
        _LOGGER.debug("Firmware sensor updated: %s", self.native_value)
