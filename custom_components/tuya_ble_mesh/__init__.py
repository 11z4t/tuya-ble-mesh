"""Tuya BLE Mesh integration for Home Assistant.

Provides local BLE mesh control of Tuya/Telink-based devices
(lights, switches) without cloud dependency.
"""

from __future__ import annotations

import pathlib
import sys

# Ensure the bundled lib/ directory is importable as top-level packages.
# HA does not automatically add custom_components/<domain>/lib/ to sys.path.
_LIB_DIR = str(pathlib.Path(__file__).parent / "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)  # ADR-012: approved sys.path manipulation for lib/ loading

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)

from custom_components.tuya_ble_mesh.const import (
    CONF_DEVICE_TYPE,
    CONF_MAC_ADDRESS,
    DEVICE_MODEL_NAMES,
    DOMAIN,
    KNOWN_VENDOR_IDS,
    PLATFORMS,
)
from custom_components.tuya_ble_mesh.device_factory import create_device
from custom_components.tuya_ble_mesh.device_registry import TuyaBLEMeshDeviceRegistry

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant, ServiceCall
    from homeassistant.helpers.device_registry import DeviceInfo

    from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class TuyaBLEMeshRuntimeData:
    """Runtime data stored in config entry for Tuya BLE Mesh.

    Typed container replacing the untyped hass.data dict.
    Accessible as entry.runtime_data in all platform setup functions.
    """

    coordinator: TuyaBLEMeshCoordinator
    device_info: DeviceInfo
    cancel_listeners: list[Callable[[], None]] = field(default_factory=list)
    registry: TuyaBLEMeshDeviceRegistry | None = None


# Type alias for typed config entry access in platform files.
# ConfigEntry became generic in HA 2024.x — guard for older versions.
if TYPE_CHECKING:
    TuyaBLEMeshConfigEntry: TypeAlias = ConfigEntry[TuyaBLEMeshRuntimeData]
else:
    TuyaBLEMeshConfigEntry = ConfigEntry


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

    # PLAT-759: Routine setup logging at DEBUG level
    _LOGGER.debug("Setting up Tuya BLE Mesh entry: %s", entry.title)

    mac_address: str = entry.data[CONF_MAC_ADDRESS]
    device_type: str = entry.data.get(CONF_DEVICE_TYPE, "")

    # BLE Proxy support: use HA's bluetooth stack to find devices
    # This routes through all available BLE adapters and ESPHome proxies
    # PLAT-737: ALWAYS use connectable=True to signal connection intent
    def _ble_device_from_ha(address: str) -> Any:
        from homeassistant.components.bluetooth import async_ble_device_from_address

        # PLAT-737: connectable=True signals HA to pause scanning during connect
        device = async_ble_device_from_address(hass, address, connectable=True)
        if device is None:
            _LOGGER.warning("BLE device %s not found via HA bluetooth stack", address)
        else:
            _LOGGER.debug("BLE device %s resolved via HA bluetooth stack", address)
        return device

    # PLAT-739: Gracefully handle missing provisioning keys for SIG Mesh devices
    try:
        device = create_device(
            device_type,
            mac_address,
            entry.data,
            ble_device_callback=_ble_device_from_ha,
        )
    except ValueError as exc:
        _LOGGER.error(
            "Failed to create device for entry %s (%s): %s",
            entry.title,
            mac_address,
            exc,
        )
        # Config entry setup fails — device will not be loaded.
        # User must remove the entry and re-provision the device.
        return False

    coordinator = TuyaBLEMeshCoordinator(device, hass=hass, entry_id=entry.entry_id, entry=entry)

    from homeassistant.helpers.device_registry import DeviceInfo

    # Create device_info (firmware version will be updated by coordinator after connection)
    # Look up manufacturer name from vendor ID
    _vid_normalized = vendor_id_hex.lower().lstrip("0x") or "0"
    # Normalize: strip '0x' prefix if present, keep lowercase hex
    if vendor_id_hex.lower().startswith("0x"):
        _vid_normalized = vendor_id_hex[2:].lower()
    else:
        _vid_normalized = vendor_id_hex.lower()
    vendor_name = KNOWN_VENDOR_IDS.get(_vid_normalized, "Tuya / Telink")

    device_info = DeviceInfo(
        identifiers={(DOMAIN, mac_address)},
        name=entry.title,
        manufacturer=vendor_name,
        model=DEVICE_MODEL_NAMES.get(device_type, "BLE Mesh Device"),
        sw_version=None,  # Will be populated by coordinator after connection
        connections={("mac", mac_address)},
    )

    # Initialize device registry and register this device
    registry = TuyaBLEMeshDeviceRegistry(hass)
    await registry.async_load()
    registry.register_device(mac_address, entry.title, device_type or "unknown")

    # Store runtime data BEFORE async_start to avoid race condition
    # (callbacks may fire during async_start and need access to runtime_data)
    entry.runtime_data = TuyaBLEMeshRuntimeData(
        coordinator=coordinator,
        device_info=device_info,
        registry=registry,
    )

    # PLAT-743: Try initial connection synchronously during setup.
    # If it fails, raise ConfigEntryNotReady to let HA Core handle retry scheduling.
    # This gives HA visibility into integration health and proper retry state in UI.
    try:
        await coordinator.async_initial_connect()
    except Exception as err:
        # Classify error to determine if it's auth or connection failure
        from custom_components.tuya_ble_mesh.error_classifier import ErrorClass

        error_class = coordinator._classify_error(err)

        if error_class == ErrorClass.MESH_AUTH:
            # Mesh authentication failed — likely invalid provisioning keys
            _LOGGER.error(
                "Mesh authentication failed for %s: %s — re-provision required",
                mac_address,
                err,
            )
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN,
                translation_key="mesh_auth_failed",
            ) from err

        # All other errors: BLE connection failure, device offline, etc.
        _LOGGER.warning(
            "Initial connection failed for %s: %s — will retry automatically",
            mac_address,
            err,
        )
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="device_connection_failed",
        ) from err

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

    # Register BLE callback for auto-reconnect when configured device reappears
    try:
        from homeassistant.components.bluetooth import (
            BluetoothCallbackMatcher,
            BluetoothChange,
            BluetoothServiceInfoBleak,
            async_register_callback,
        )

        def _on_ble_device_found(
            service_info: BluetoothServiceInfoBleak,
            change: BluetoothChange,
        ) -> None:
            if not coordinator.state.available:
                # PLAT-759: Routine reconnect trigger at DEBUG level
                _LOGGER.debug(
                    "BLE device %s reappeared (RSSI: %s) — triggering reconnect",
                    service_info.address,
                    service_info.rssi,
                )
                coordinator.schedule_reconnect()

        entry.async_on_unload(
            async_register_callback(
                hass,
                _on_ble_device_found,
                BluetoothCallbackMatcher(address=mac_address),
                BluetoothChange.ADVERTISEMENT,
            )
        )
        _LOGGER.debug("BLE reconnect callback registered for %s", mac_address)
    except ImportError:
        _LOGGER.debug("Bluetooth integration not available, skipping BLE reconnect callback")

    # PLAT-759: Routine setup completion at DEBUG level
    _LOGGER.debug("Tuya BLE Mesh entry set up: %s", entry.title)
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
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
                translation_placeholders={"device_id": device_id},
            )
        try:
            device = coordinator.device
            if hasattr(device, "send_power"):
                # Flash: off/on x3, with 0.5s delay between each command
                for _ in range(3):
                    await device.send_power(False)
                    await asyncio.sleep(0.5)
                    await device.send_power(True)
                    await asyncio.sleep(0.5)
        except Exception as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="identify_failed",
                translation_placeholders={"error": str(exc)},
            ) from exc

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

    async def handle_get_diagnostics(call: ServiceCall) -> dict[str, Any]:
        """Get diagnostic information for a device.

        Args:
            call: Service call with device_id field.
        """
        device_id: str = call.data.get("device_id", "")
        coordinator = _get_coordinator_for_device(hass, device_id)
        if coordinator is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
                translation_placeholders={"device_id": device_id},
            )

        stats = coordinator.statistics
        diagnostics = {
            "device_address": coordinator.device.address,
            "available": coordinator.state.available,
            "connection_uptime": f"{stats.connection_uptime:.1f}s",
            "total_reconnects": stats.total_reconnects,
            "total_errors": stats.total_errors,
            "connection_errors": stats.connection_errors,
            "command_errors": stats.command_errors,
            "avg_response_time": (
                f"{stats.avg_response_time:.3f}s" if stats.response_times else "N/A"
            ),
            "rssi_dbm": coordinator.state.rssi,
            "firmware_version": coordinator.state.firmware_version,
            "last_error": stats.last_error,
            "last_disconnect": stats.last_disconnect_time,
        }
        _LOGGER.info("Diagnostics for %s: %s", device_id, diagnostics)
        return diagnostics

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

    async def handle_reconnect(call: ServiceCall) -> None:
        """Force reconnect a device.

        Args:
            call: Service call with device_id field.
        """
        device_id: str = call.data.get("device_id", "")
        coordinator = _get_coordinator_for_device(hass, device_id)
        if coordinator is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
                translation_placeholders={"device_id": device_id},
            )

        with contextlib.suppress(OSError):
            await coordinator.device.disconnect()
        coordinator.schedule_reconnect()
        _LOGGER.info("Reconnect scheduled for %s", device_id)

    hass.services.async_register(
        DOMAIN,
        "get_diagnostics",
        handle_get_diagnostics,
        schema=vol.Schema({vol.Required("device_id"): str}),
    )
    hass.services.async_register(
        DOMAIN,
        "reconnect",
        handle_reconnect,
        schema=vol.Schema({vol.Required("device_id"): str}),
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


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: TuyaBLEMeshConfigEntry,
    device_entry: Any,
) -> bool:
    """Return True if the device can be removed from the HA device registry.

    Allows removal of stale devices that are no longer connected to the mesh.
    Active (connected) devices return False to prevent accidental removal.

    This is called when the user clicks 'Delete' on a device in the HA UI.
    Unlike reauth or unload, this permanently removes the device entry from
    HA's device registry.

    Args:
        hass: Home Assistant instance.
        entry: Config entry associated with the device.
        device_entry: HA device registry entry to be removed.

    Returns:
        True if the device is not currently connected (safe to remove),
        False if the device is active and should not be removed.
    """
    runtime: TuyaBLEMeshRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is None:
        # Entry has no runtime data — not loaded, allow cleanup
        return True

    # Allow removal only when device is not currently connected.
    # This prevents accidentally removing an active device while keeping
    # the UI clean of stale entries that can never reconnect.
    is_connected = runtime.coordinator.state.available
    if is_connected:
        _LOGGER.warning(
            "Refusing removal of active device %s (still connected to mesh)",
            entry.title,
        )
    else:
        _LOGGER.info("Allowing removal of stale device %s (not connected)", entry.title)

    return not is_connected


async def async_unload_entry(hass: HomeAssistant, entry: TuyaBLEMeshConfigEntry) -> bool:
    """Unload a Tuya BLE Mesh config entry.

    Stops the coordinator and cleans up entry data.

    Args:
        hass: Home Assistant instance.
        entry: Config entry to unload.

    Returns:
        True if unload succeeded.
    """
    # PLAT-759: Routine unload logging at DEBUG level
    _LOGGER.debug("Unloading Tuya BLE Mesh entry: %s", entry.title)

    runtime: TuyaBLEMeshRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        for cancel in runtime.cancel_listeners:
            cancel()
        await runtime.coordinator.async_stop()

    unload_ok: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # PLAT-759: Routine unload completion at DEBUG level
    _LOGGER.debug("Tuya BLE Mesh entry unloaded: %s (ok=%s)", entry.title, unload_ok)
    return unload_ok
