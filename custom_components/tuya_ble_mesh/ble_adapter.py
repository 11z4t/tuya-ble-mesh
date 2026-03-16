"""HA Bluetooth adapter layer for tuya_ble_mesh.

Monkey-patches lib/ modules at runtime to use HA's bluetooth integration for:
- Scanner coordination (pause/resume during connect via bleak_retry_connector)
- ESPHome Bluetooth Proxy support (BLEDevice re-resolution from HA)
- Retry resilience (ble_device_callback for stale device re-resolution)

S1 Library Isolation: lib/ files are NOT modified on disk.
Module-level references are patched at runtime before device creation.

PLAT-737: Migrera BLE-lager till HA Bluetooth API + ESPHome Proxy-stöd.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Original references stored for cleanup
_originals: dict[str, Any] = {}
_patched = False


def patch_lib_for_ha(hass: HomeAssistant) -> None:
    """Patch lib/ BLE modules to use HA's bluetooth stack.

    Must be called ONCE before creating any device instances.

    Patches:
    - tuya_ble_mesh.connection.establish_connection
      → Adds ble_device_callback for HA BLE device re-resolution during retries.
        This allows bleak_retry_connector to coordinate scanner pause/resume
        via habluetooth metadata in the BLEDevice.

    - tuya_ble_mesh.sig_mesh_device.SIGMeshDevice.connect
      → Replaces raw BleakClient().connect() with bleak_retry_connector's
        establish_connection, which properly coordinates with HA's scanner.
    """
    global _patched
    if _patched:
        _LOGGER.debug("lib/ BLE modules already patched for HA")
        return

    import tuya_ble_mesh.connection as conn_mod
    import tuya_ble_mesh.sig_mesh_device as sig_mod

    # Store originals for unpatch
    _originals["conn_establish"] = conn_mod.establish_connection
    _originals["sig_connect"] = sig_mod.SIGMeshDevice.connect

    # --- Patch 1: BLEConnection.establish_connection ---
    _patch_establish_connection(hass, conn_mod)

    # --- Patch 2: SIGMeshDevice.connect ---
    _patch_sig_mesh_connect(hass, sig_mod)

    _patched = True
    _LOGGER.info("Patched lib/ BLE modules for HA bluetooth coordination (PLAT-737)")


def unpatch_lib_for_ha() -> None:
    """Restore original lib/ module references."""
    global _patched
    if not _originals:
        return

    import tuya_ble_mesh.connection as conn_mod
    import tuya_ble_mesh.sig_mesh_device as sig_mod

    if "conn_establish" in _originals:
        conn_mod.establish_connection = _originals["conn_establish"]
    if "sig_connect" in _originals:
        sig_mod.SIGMeshDevice.connect = _originals["sig_connect"]

    _originals.clear()
    _patched = False
    _LOGGER.info("Restored original lib/ BLE modules")


def _patch_establish_connection(hass: HomeAssistant, conn_mod: Any) -> None:
    """Replace establish_connection in connection.py with HA-aware version.

    The HA-aware version:
    1. Re-resolves the BLEDevice from HA for fresh adapter metadata
    2. Provides a ble_device_callback so bleak_retry_connector can
       re-resolve during retries (handles stale BLEDevice objects)
    3. Delegates to the original bleak_retry_connector.establish_connection
       which coordinates scanner pause/resume via habluetooth
    """
    from bleak_retry_connector import establish_connection as raw_establish

    async def ha_establish_connection(
        client_class: type,
        device: Any,
        name: str,
        disconnected_callback: Any = None,
        max_attempts: int = 3,
        cached_services: Any = None,
        ble_device_callback: Any = None,
        use_services_cache: bool = True,
    ) -> Any:
        from homeassistant.components.bluetooth import async_ble_device_from_address

        # Re-resolve from HA for fresh adapter metadata and ESPHome proxy routing
        ha_device = async_ble_device_from_address(hass, name, connectable=True)
        if ha_device is not None:
            device = ha_device
            _LOGGER.debug(
                "BLE device %s resolved via HA bluetooth stack (RSSI: %s)",
                name,
                getattr(ha_device, "rssi", "?"),
            )

        # Always provide HA re-resolution callback for retries
        if ble_device_callback is None:

            def _ha_resolve() -> Any:
                return (
                    async_ble_device_from_address(hass, name, connectable=True)
                    or device
                )

            ble_device_callback = _ha_resolve

        return await raw_establish(
            client_class,
            device,
            name,
            disconnected_callback=disconnected_callback,
            max_attempts=max_attempts,
            cached_services=cached_services,
            ble_device_callback=ble_device_callback,
            use_services_cache=use_services_cache,
        )

    conn_mod.establish_connection = ha_establish_connection


def _patch_sig_mesh_connect(hass: HomeAssistant, sig_mod: Any) -> None:
    """Replace SIGMeshDevice.connect with HA-aware version.

    The original connect() uses raw BleakClient().connect() which does NOT
    coordinate with HA's bluetooth scanner → BlueZ returns 0x0a Busy.

    The patched version uses bleak_retry_connector.establish_connection which:
    1. Detects habluetooth scanner via BLEDevice metadata
    2. Pauses scanner during connection attempt
    3. Resumes scanner on disconnect
    4. Supports ESPHome Bluetooth Proxy routing
    """
    from bleak_retry_connector import establish_connection as raw_establish

    # Import constants from sig_mesh_device module
    SIG_MESH_PROXY_DATA_OUT = sig_mod.SIG_MESH_PROXY_DATA_OUT
    _BLUEZ_CACHE_CLEAR_DELAY = sig_mod._BLUEZ_CACHE_CLEAR_DELAY

    async def ha_sig_connect(
        self: Any,
        timeout: float = 10.0,
        max_retries: int = 5,
    ) -> None:
        """HA-aware SIG Mesh connect using establish_connection.

        Uses bleak_retry_connector.establish_connection instead of raw
        BleakClient.connect() for HA scanner coordination and ESPHome
        Bluetooth Proxy support.
        """
        from bleak import BleakClient, BleakScanner
        from bleak.exc import BleakDBusError, BleakError

        from tuya_ble_mesh.exceptions import ConnectionError as MeshConnectionError
        from tuya_ble_mesh.exceptions import SIGMeshError
        from tuya_ble_mesh.logging_context import mesh_operation

        from homeassistant.components.bluetooth import async_ble_device_from_address

        async with mesh_operation(self._address, "connect"):
            await self._load_keys()

            last_error: Exception | None = None
            for attempt in range(1, max_retries + 1):
                try:
                    _LOGGER.info(
                        "Connecting to %s (attempt %d/%d, HA-managed)",
                        self._address,
                        attempt,
                        max_retries,
                    )

                    # Resolve BLE device — prefer HA stack, fall back to scan
                    device = None
                    if self._ble_device_callback is not None:
                        device = self._ble_device_callback(self._address)

                    # Re-resolve from HA for fresh adapter metadata
                    ha_device = async_ble_device_from_address(
                        hass, self._address, connectable=True
                    )
                    if ha_device is not None:
                        device = ha_device

                    # Last resort: direct BleakScanner
                    if device is None:
                        scan_kwargs: dict[str, Any] = {"timeout": timeout}
                        if self._adapter is not None:
                            scan_kwargs["adapter"] = self._adapter
                        device = await BleakScanner.find_device_by_address(
                            self._address, **scan_kwargs
                        )

                    if device is None:
                        msg = (
                            f"Device {self._address} not found in BLE scan. "
                            "Ensure device is powered on and in range of a BLE "
                            "adapter or ESPHome proxy."
                        )
                        raise MeshConnectionError(msg)

                    # HA re-resolution callback for retries within establish_connection
                    def _ha_resolve() -> Any:
                        return (
                            async_ble_device_from_address(
                                hass, self._address, connectable=True
                            )
                            or device
                        )

                    # Use establish_connection for HA scanner coordination
                    # instead of raw BleakClient().connect()
                    client = await raw_establish(
                        BleakClient,
                        device,
                        self._address,
                        disconnected_callback=self._on_ble_disconnect,
                        max_attempts=3,
                        ble_device_callback=_ha_resolve,
                    )

                    # Subscribe to Proxy Data Out notifications
                    try:
                        await client.start_notify(
                            SIG_MESH_PROXY_DATA_OUT, self._on_notify
                        )
                    except (
                        EOFError,
                        BleakError,
                        BleakDBusError,
                        OSError,
                    ) as notify_exc:
                        _LOGGER.warning(
                            "Notification subscription failed for %s: %s (%s) — "
                            "device will work but won't receive push status updates",
                            self._address,
                            notify_exc,
                            type(notify_exc).__name__,
                        )

                    self._client = client
                    _LOGGER.info(
                        "Connected to %s (HA-managed, attempt %d)",
                        self._address,
                        attempt,
                    )

                    # Request Composition Data (non-critical)
                    try:
                        await self.request_composition_data()
                    except (TimeoutError, SIGMeshError, BleakError):
                        _LOGGER.debug(
                            "Composition Data request failed (non-critical)",
                            exc_info=True,
                        )
                    return

                except (BleakError, MeshConnectionError, OSError) as exc:
                    last_error = exc
                    _LOGGER.warning(
                        "Connection attempt %d failed for %s: %s",
                        attempt,
                        self._address,
                        exc,
                    )
                    # Remove cached BLE device between retries
                    await self._bluetoothctl_remove()
                    await asyncio.sleep(_BLUEZ_CACHE_CLEAR_DELAY)

            msg = f"Failed to connect to {self._address} after {max_retries} attempts"
            raise MeshConnectionError(msg) from last_error

    sig_mod.SIGMeshDevice.connect = ha_sig_connect
