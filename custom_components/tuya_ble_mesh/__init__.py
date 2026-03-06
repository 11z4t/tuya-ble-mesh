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
    CONF_DEVICE_TYPE,
    CONF_IV_INDEX,
    CONF_MAC_ADDRESS,
    CONF_MESH_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_OP_ITEM_PREFIX,
    CONF_UNICAST_OUR,
    CONF_UNICAST_TARGET,
    CONF_VENDOR_ID,
    DEFAULT_IV_INDEX,
    DEFAULT_MESH_ADDRESS,
    DEFAULT_OP_ITEM_PREFIX,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_SIG_PLUG,
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
    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

    _LOGGER.info("Setting up Tuya BLE Mesh entry: %s", entry.title)

    mac_address: str = entry.data[CONF_MAC_ADDRESS]
    device_type: str = entry.data.get(CONF_DEVICE_TYPE, "")

    if device_type == DEVICE_TYPE_SIG_PLUG:
        from tuya_ble_mesh.secrets import SecretsManager
        from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice

        target_addr = int(entry.data.get(CONF_UNICAST_TARGET, "00aa"), 16)
        our_addr = int(entry.data.get(CONF_UNICAST_OUR, "0001"), 16)
        op_prefix: str = entry.data.get(CONF_OP_ITEM_PREFIX, DEFAULT_OP_ITEM_PREFIX)
        iv_index: int = entry.data.get(CONF_IV_INDEX, DEFAULT_IV_INDEX)

        device = SIGMeshDevice(
            mac_address,
            target_addr,
            our_addr,
            SecretsManager(),
            op_item_prefix=op_prefix,
            iv_index=iv_index,
        )
    else:
        from tuya_ble_mesh.device import MeshDevice

        mesh_name: str = entry.data[CONF_MESH_NAME]
        mesh_password: str = entry.data[CONF_MESH_PASSWORD]
        vendor_id_hex: str = entry.data.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID)
        vendor_id_int = int(vendor_id_hex, 16)
        vendor_id_bytes = vendor_id_int.to_bytes(2, "little")

        mesh_addr: int = entry.data.get(CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS)

        device = MeshDevice(  # type: ignore[assignment]
            mac_address,
            mesh_name.encode(),
            mesh_password.encode(),
            mesh_id=mesh_addr,
            vendor_id=vendor_id_bytes,
        )

    coordinator = TuyaBLEMeshCoordinator(device, hass=hass, entry_id=entry.entry_id)

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
