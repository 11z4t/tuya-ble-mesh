"""Tuya BLE Mesh integration for Home Assistant.

Provides local BLE mesh control of Tuya/Telink-based devices
(lights, switches) without cloud dependency.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError

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
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers.device_registry import DeviceInfo

    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

_LOGGER = logging.getLogger(__name__)

# Make lib/tuya_ble_mesh importable — check bundled copy first, then dev layout
_BUNDLED_LIB = str(Path(__file__).resolve().parent / "lib")
_DEV_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
for _lib_dir in (_BUNDLED_LIB, _DEV_LIB):
    if Path(_lib_dir).is_dir() and _lib_dir not in sys.path:
        sys.path.insert(0, _lib_dir)
        break


@dataclass
class TuyaBLEMeshRuntimeData:
    """Runtime data stored in config entry for Tuya BLE Mesh.

    Typed container replacing the untyped hass.data dict.
    Accessible as entry.runtime_data in all platform setup functions.
    """

    coordinator: TuyaBLEMeshCoordinator
    device_info: DeviceInfo
    cancel_listeners: list[Callable[[], None]] = field(default_factory=list)


# Type alias for typed config entry access in platform files
TuyaBLEMeshConfigEntry = ConfigEntry[TuyaBLEMeshRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: TuyaBLEMeshConfigEntry) -> bool:
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

        # Try connectable first, fall back to any advertisement
        device = async_ble_device_from_address(hass, address, connectable=True)
        if device is None:
            device = async_ble_device_from_address(hass, address, connectable=False)
        if device is None:
            _LOGGER.warning("BLE device %s not found via HA bluetooth stack", address)
        else:
            _LOGGER.debug("BLE device %s resolved via HA bluetooth stack", address)
        return device

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

    # Store runtime data in entry (typed, modern HA pattern)
    entry.runtime_data = TuyaBLEMeshRuntimeData(
        coordinator=coordinator,
        device_info=device_info,
    )

    await coordinator.async_start()

    # Forward platform setup even if device is unavailable —
    # entities will show as "unavailable" until connection succeeds
    # Pre-import platform modules in executor to avoid blocking the event loop
    # (HA 2026.x raises warnings for synchronous imports during setup)
    import importlib

    for platform in PLATFORMS:
        await hass.async_add_import_executor_job(importlib.import_module, f".{platform}", __name__)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services
    await _async_register_services(hass)

    # Reload entry when options are changed
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    _LOGGER.info("Tuya BLE Mesh entry set up: %s", entry.title)
    return True


async def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration services if not already registered.

    Args:
        hass: Home Assistant instance.
    """
    import voluptuous as vol

    if hass.services.has_service(DOMAIN, "identify"):
        return  # Already registered

    async def handle_identify(call: ServiceCall) -> None:
        """Flash device LED for identification.

        Args:
            call: Service call with device_id field.
        """
        device_id: str = call.data.get("device_id", "")
        coordinator = _get_coordinator_for_device(hass, device_id)
        if coordinator is None:
            raise HomeAssistantError(f"Device not found: {device_id}")
        try:
            device = coordinator.device
            if hasattr(device, "send_power"):
                # Flash: off → on → off → on
                for _ in range(3):
                    await device.send_power(False)
                    await device.send_power(True)
        except Exception as exc:
            raise HomeAssistantError(f"Failed to identify device: {exc}") from exc

    async def handle_set_log_level(call: ServiceCall) -> None:
        """Change BLE mesh logging verbosity without HA restart.

        Args:
            call: Service call with level field (debug/info/warning/error).
        """
        import logging as _logging

        level_str: str = call.data.get("level", "info").upper()
        level = getattr(_logging, level_str, _logging.INFO)
        _logging.getLogger("tuya_ble_mesh").setLevel(level)
        _LOGGER.info("Log level set to %s", level_str)

    hass.services.async_register(
        DOMAIN,
        "identify",
        handle_identify,
        schema=vol.Schema({vol.Required("device_id"): str}),
    )
    hass.services.async_register(
        DOMAIN,
        "set_log_level",
        handle_set_log_level,
        schema=vol.Schema(
            {
                vol.Required("level"): vol.In(["debug", "info", "warning", "error"]),
            }
        ),
    )


def _get_coordinator_for_device(
    hass: HomeAssistant, device_id: str
) -> TuyaBLEMeshCoordinator | None:
    """Find coordinator for a given device_id from the device registry.

    Args:
        hass: Home Assistant instance.
        device_id: HA device registry device_id.

    Returns:
        Coordinator if found, None otherwise.
    """
    from homeassistant.helpers import device_registry as dr

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if device is None:
        return None

    # Match by config entry ID
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is not None and hasattr(entry, "runtime_data"):
            runtime: TuyaBLEMeshRuntimeData = entry.runtime_data
            return runtime.coordinator
    return None


async def _async_update_listener(hass: HomeAssistant, entry: TuyaBLEMeshConfigEntry) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: TuyaBLEMeshConfigEntry) -> bool:
    """Unload a Tuya BLE Mesh config entry.

    Stops the coordinator and cleans up entry data.

    Args:
        hass: Home Assistant instance.
        entry: Config entry to unload.

    Returns:
        True if unload succeeded.
    """
    _LOGGER.info("Unloading Tuya BLE Mesh entry: %s", entry.title)

    runtime: TuyaBLEMeshRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        for cancel in runtime.cancel_listeners:
            cancel()
        await runtime.coordinator.async_stop()

    unload_ok: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    _LOGGER.info("Tuya BLE Mesh entry unloaded: %s (ok=%s)", entry.title, unload_ok)
    return unload_ok
