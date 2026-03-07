"""Tuya BLE Mesh integration for Home Assistant.

Provides local BLE mesh control of Tuya/Telink-based devices
(lights, switches) without cloud dependency.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from custom_components.tuya_ble_mesh.const import (
    CONF_APP_KEY,
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_DEV_KEY,
    CONF_DEVICE_TYPE,
    CONF_IV_INDEX,
    CONF_MAC_ADDRESS,
    CONF_MESH_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_NET_KEY,
    CONF_UNICAST_OUR,
    CONF_UNICAST_TARGET,
    CONF_VENDOR_ID,
    DEFAULT_BRIDGE_PORT,
    DEFAULT_IV_INDEX,
    DEFAULT_MESH_ADDRESS,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
    DOMAIN,
    PLATFORMS,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Make lib/tuya_ble_mesh importable — check bundled copy first, then dev layout
_BUNDLED_LIB = str(Path(__file__).resolve().parent / "lib")
_DEV_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
for _lib_dir in (_BUNDLED_LIB, _DEV_LIB):
    if Path(_lib_dir).is_dir() and _lib_dir not in sys.path:
        sys.path.insert(0, _lib_dir)
        break


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

    # BLE Proxy support: use HA's bluetooth stack to find devices
    # This routes through all available BLE adapters and ESPHome proxies
    def _ble_device_from_ha(address: str) -> Any:
        from homeassistant.components.bluetooth import async_ble_device_from_address

        return async_ble_device_from_address(hass, address, connectable=True)

    if device_type == DEVICE_TYPE_SIG_BRIDGE_PLUG:
        from tuya_ble_mesh.sig_mesh_bridge import SIGMeshBridgeDevice

        target_addr = int(entry.data.get(CONF_UNICAST_TARGET, "00B0"), 16)
        bridge_host: str = entry.data[CONF_BRIDGE_HOST]
        bridge_port: int = entry.data.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)

        device = SIGMeshBridgeDevice(
            mac_address,
            target_addr,
            bridge_host,
            bridge_port,
        )
    elif device_type == DEVICE_TYPE_TELINK_BRIDGE_LIGHT:
        from tuya_ble_mesh.sig_mesh_bridge import TelinkBridgeDevice

        bridge_host = entry.data[CONF_BRIDGE_HOST]
        bridge_port = entry.data.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)

        device = TelinkBridgeDevice(
            mac_address,
            bridge_host,
            bridge_port,
        )
    elif device_type == DEVICE_TYPE_SIG_PLUG:
        from tuya_ble_mesh.secrets import DictSecretsManager
        from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice

        target_addr = int(entry.data.get(CONF_UNICAST_TARGET, "00B0"), 16)
        our_addr = int(entry.data.get(CONF_UNICAST_OUR, "0001"), 16)
        iv_index: int = entry.data.get(CONF_IV_INDEX, DEFAULT_IV_INDEX)

        # Build secrets dict from config entry keys
        target_hex = f"{target_addr:04x}"
        op_prefix = "cfg"
        secrets_dict = {
            f"{op_prefix}-net-key/password": entry.data.get(CONF_NET_KEY, ""),
            f"{op_prefix}-dev-key-{target_hex}/password": entry.data.get(CONF_DEV_KEY, ""),
            f"{op_prefix}-app-key/password": entry.data.get(CONF_APP_KEY, ""),
        }

        device = SIGMeshDevice(
            mac_address,
            target_addr,
            our_addr,
            DictSecretsManager(secrets_dict),
            op_item_prefix=op_prefix,
            iv_index=iv_index,
            ble_device_callback=_ble_device_from_ha,
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
            ble_device_callback=_ble_device_from_ha,
        )

    coordinator = TuyaBLEMeshCoordinator(device, hass=hass, entry_id=entry.entry_id)

    from homeassistant.helpers.device_registry import DeviceInfo

    device_info = DeviceInfo(
        identifiers={(DOMAIN, mac_address)},
        name=entry.title,
        manufacturer="Malmbergs / Tuya",
        model=device_type or "BLE Mesh",
        sw_version=device.firmware_version,
        connections={("mac", mac_address)},
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "device_info": device_info,
    }

    await coordinator.async_start()

    # Forward platform setup even if device is unavailable —
    # entities will show as "unavailable" until connection succeeds
    # Pre-import platform modules in executor to avoid blocking the event loop
    # (HA 2026.x raises warnings for synchronous imports during setup)
    import importlib
    for platform in PLATFORMS:
        await hass.async_add_import_executor_job(
            importlib.import_module, f".{platform}", __name__
        )

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
