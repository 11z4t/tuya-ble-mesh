"""Tuya BLE Mesh integration for Home Assistant.

Provides local BLE mesh control of Tuya/Telink-based devices
(lights, switches) without cloud dependency.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from custom_components.tuya_ble_mesh.const import (
    CONF_MAC_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DOMAIN,
    PLATFORMS,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Make lib/tuya_ble_mesh importable alongside the HA component
_LIB_DIR = str(Path(__file__).resolve().parent.parent.parent / "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Tuya BLE Mesh from a config entry.

    Creates a MeshDevice and TuyaBLEMeshCoordinator, starts the
    coordinator, and forwards platform setup.

    Args:
        hass: Home Assistant instance.
        entry: Config entry to set up.

    Returns:
        True if setup succeeded.
    """
    from tuya_ble_mesh.device import MeshDevice

    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

    _LOGGER.info("Setting up Tuya BLE Mesh entry: %s", entry.title)

    mac_address: str = entry.data[CONF_MAC_ADDRESS]
    mesh_name: str = entry.data[CONF_MESH_NAME]
    mesh_password: str = entry.data[CONF_MESH_PASSWORD]

    device = MeshDevice(
        mac_address,
        mesh_name.encode(),
        mesh_password.encode(),
    )
    coordinator = TuyaBLEMeshCoordinator(device)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
    }

    await coordinator.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info("Tuya BLE Mesh entry set up: %s", entry.title)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Tuya BLE Mesh config entry.

    Stops the coordinator and cleans up entry data.

    Args:
        hass: Home Assistant instance.
        entry: Config entry to unload.

    Returns:
        True if unload succeeded.
    """
    _LOGGER.info("Unloading Tuya BLE Mesh entry: %s", entry.title)

    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = entry_data.get("coordinator")
    if coordinator is not None:
        await coordinator.async_stop()

    unload_ok: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)

    return unload_ok
