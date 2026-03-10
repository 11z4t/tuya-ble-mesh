"""Firmware update entity for Tuya BLE Mesh devices.

Exposes the device firmware version string from Composition Data (SIG Mesh)
or device attributes (Telink Mesh) as an HA UpdateEntity. This allows users
to see what firmware version is running without needing to check diagnostics.

The entity is read-only — firmware flashing is handled externally (e.g. via
the Malmbergs app or SIG Mesh OTA protocol). No install() method is provided.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.tuya_ble_mesh.const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up update entities for a Tuya BLE Mesh config entry."""
    runtime_data = entry.runtime_data
    coordinator: TuyaBLEMeshCoordinator = runtime_data.coordinator
    device_info = runtime_data.device_info

    async_add_entities(
        [TuyaBLEMeshFirmwareUpdateEntity(coordinator, device_info, entry.entry_id)]
    )


class TuyaBLEMeshFirmwareUpdateEntity(
    CoordinatorEntity["TuyaBLEMeshCoordinator"], UpdateEntity
):
    """Read-only firmware version entity for a Tuya BLE Mesh device.

    Reports the firmware version string received via Composition Data (SIG Mesh)
    or the device attribute set during connect. No OTA install supported — version
    display only.
    """

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_has_entity_name = True
    _attr_name = "Firmware"

    def __init__(
        self,
        coordinator: TuyaBLEMeshCoordinator,
        device_info: object,
        entry_id: str,
    ) -> None:
        """Initialise the firmware update entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry_id}_firmware"
        self._attr_device_info = device_info  # type: ignore[assignment]

    @property
    def installed_version(self) -> str | None:
        """Return the currently-installed firmware version, or None if unknown."""
        return self.coordinator.state.firmware_version

    @property
    def latest_version(self) -> str | None:
        """Return None — we don't have access to the latest release information.

        The HA update entity will show 'Up to date' when installed == latest,
        and hide the entity when both are None. Returning None here means HA
        will show the installed version but without an 'update available' badge.
        """
        return self.coordinator.state.firmware_version

    @property
    def available(self) -> bool:
        """Entity is available when the device is reachable."""
        return self.coordinator.state.available

    @property
    def release_notes(self) -> str | None:
        """No release notes available from device."""
        return None
