"""Sensor entities for Tuya BLE Mesh devices.

Provides RSSI (signal strength), firmware version, power, and energy sensors.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory

from custom_components.tuya_ble_mesh.const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

    AddEntitiesCallback = Callable[..., None]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tuya BLE Mesh sensor entities from a config entry.

    Args:
        hass: Home Assistant instance.
        entry: Config entry being set up.
        async_add_entities: Callback to register new entities.
    """
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: TuyaBLEMeshCoordinator = entry_data["coordinator"]
    device_info: DeviceInfo = entry_data["device_info"]

    entities: list[SensorEntity] = [
        TuyaBLEMeshRSSISensor(coordinator, entry.entry_id, device_info),
        TuyaBLEMeshFirmwareSensor(coordinator, entry.entry_id, device_info),
    ]

    # Add power/energy sensors only if the device supports power monitoring.
    # Most BLE Mesh plugs (e.g. Malmbergs S17) do NOT have power metering.
    if getattr(coordinator.device, "supports_power_monitoring", False):
        entities.append(TuyaBLEMeshPowerSensor(coordinator, entry.entry_id, device_info))
        entities.append(TuyaBLEMeshEnergySensor(coordinator, entry.entry_id, device_info))

    async_add_entities(entities)


class TuyaBLEMeshRSSISensor(SensorEntity):
    """RSSI signal strength sensor for a Tuya BLE Mesh device."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "RSSI"

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator.device.address}_rssi"
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
    def native_value(self) -> int | None:
        """Return the RSSI value in dBm."""
        return self._coordinator.state.rssi

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "dBm"

    @property
    def device_class(self) -> SensorDeviceClass:
        """Return the device class."""
        return SensorDeviceClass.SIGNAL_STRENGTH

    @property
    def entity_category(self) -> EntityCategory:
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC

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


class TuyaBLEMeshFirmwareSensor(SensorEntity):
    """Firmware version sensor for a Tuya BLE Mesh device."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Firmware"

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator.device.address}_firmware"
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
    def native_value(self) -> str | None:
        """Return the firmware version."""
        return self._coordinator.state.firmware_version

    @property
    def entity_category(self) -> EntityCategory:
        """Return the entity category."""
        return EntityCategory.DIAGNOSTIC

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


class TuyaBLEMeshPowerSensor(SensorEntity):
    """Power consumption sensor (W) for a Tuya BLE Mesh plug."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Power"

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator.device.address}_power"
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
    def native_value(self) -> float | None:
        """Return the power in watts."""
        return self._coordinator.state.power_w

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "W"

    @property
    def device_class(self) -> SensorDeviceClass:
        """Return the device class."""
        return SensorDeviceClass.POWER

    @property
    def state_class(self) -> SensorStateClass:
        """Return the state class."""
        return SensorStateClass.MEASUREMENT

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


class TuyaBLEMeshEnergySensor(SensorEntity):
    """Energy consumption sensor (kWh) for a Tuya BLE Mesh plug."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Energy"

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        entry_id: str,
        device_info: DeviceInfo | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._attr_unique_id = f"{coordinator.device.address}_energy"
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
    def native_value(self) -> float | None:
        """Return the energy in kWh."""
        return self._coordinator.state.energy_kwh

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        return "kWh"

    @property
    def device_class(self) -> SensorDeviceClass:
        """Return the device class."""
        return SensorDeviceClass.ENERGY

    @property
    def state_class(self) -> SensorStateClass:
        """Return the state class."""
        return SensorStateClass.TOTAL_INCREASING

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
