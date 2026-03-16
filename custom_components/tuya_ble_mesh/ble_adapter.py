"""BLE adapter layer for Home Assistant bluetooth API integration.

This adapter provides a bridge between lib/tuya_ble_mesh (which MUST NOT
import from homeassistant) and HA's bluetooth integration. It wraps HA's
bluetooth device discovery and managed connection APIs in a form that
lib/ can consume without breaking S1 Library Isolation.

Architecture (S1 compliance):
- lib/tuya_ble_mesh/connection.py expects: ble_device_callback(address) → BLEDevice
- This adapter provides HABluetoothAdapter that implements that interface
- HABluetoothAdapter internally uses HA's async_ble_device_from_address
- lib/ never imports homeassistant — adapter is ONLY used in custom_components/

Key patterns from research (switchbot, bthome, shelly):
1. Use async_ble_device_from_address for device lookup (no manual scanning)
2. Use establish_connection from bleak_retry_connector (not raw BleakClient)
3. Use BleakClientWithServiceCache for GATT service caching
4. Always call close_stale_connections_by_address before connecting
5. HA's bluetooth wrapper automatically manages scanning pause/resume
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from bleak_retry_connector import (
    BleakClientWithServiceCache,
    close_stale_connections_by_address,
    establish_connection,
)

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class HABluetoothAdapter:
    """Adapter that provides HA bluetooth device lookup for lib/ consumption.

    This class bridges lib/tuya_ble_mesh (which uses ble_device_callback)
    and HA's bluetooth integration, preserving S1 Library Isolation.

    Usage pattern (in custom_components/__init__.py):
        adapter = HABluetoothAdapter(hass, device_name="Tuya Light AA:BB:CC")
        device = create_device(
            device_type,
            mac_address,
            entry.data,
            ble_device_callback=adapter.get_ble_device,
            ble_connect_callback=adapter.establish_connection,
        )

    The lib/ code calls get_ble_device(address) and receives a BLEDevice
    without knowing it came from HA's bluetooth stack.
    """

    def __init__(self, hass: HomeAssistant, device_name: str = "Tuya BLE Mesh Device") -> None:
        """Initialize the adapter.

        Args:
            hass: Home Assistant instance (provides bluetooth registry access).
            device_name: Device name for logging (e.g., "Smart Plug AA:BB:CC").
        """
        self._hass = hass
        self._device_name = device_name

    def get_ble_device(self, address: str) -> Any:
        """Look up BLEDevice via HA's bluetooth registry.

        Called by lib/tuya_ble_mesh/connection.py during connect.
        Returns BLEDevice from HA's unified bluetooth registry (includes
        devices discovered via local adapters AND ESPHome BLE proxies).

        Args:
            address: BLE MAC address (e.g., "AA:BB:CC:DD:EE:FF").

        Returns:
            BLEDevice if found in HA's registry, None otherwise.
        """
        from homeassistant.components.bluetooth import async_ble_device_from_address

        # Try connectable first (for devices that need GATT connections)
        device = async_ble_device_from_address(
            self._hass, address.upper(), connectable=True
        )
        if device is None:
            # Fall back to any advertisement (passive discovery)
            device = async_ble_device_from_address(
                self._hass, address.upper(), connectable=False
            )
        if device is None:
            _LOGGER.warning(
                "BLE device %s not found in HA bluetooth registry. "
                "Ensure device is in range of a BLE adapter or ESPHome BLE proxy.",
                address,
            )
        else:
            _LOGGER.debug(
                "Resolved BLE device %s via HA bluetooth (connectable=%s, RSSI=%s)",
                address,
                getattr(device, "connectable", "?"),
                getattr(device, "rssi", "?"),
            )
        return device

    async def establish_connection(self, ble_device: Any) -> BleakClientWithServiceCache:
        """Establish a managed BLE connection to the given BLEDevice.

        Called by lib/tuya_ble_mesh/connection.py during connect.
        Uses HA's bluetooth API with automatic stale connection cleanup,
        retry logic, and service caching.

        Args:
            ble_device: BLEDevice from get_ble_device().

        Returns:
            Connected BleakClientWithServiceCache instance.

        Raises:
            BleakError: If all connection attempts fail.
            TimeoutError: If connection times out.
        """
        # Clean up stale connections (prevents "Busy" adapter errors)
        await close_stale_connections_by_address(ble_device.address)

        # Establish connection with retries and caching
        # HA's bluetooth wrapper automatically manages scanning pause/resume
        client = await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            name=self._device_name,
            max_attempts=4,
            use_services_cache=True,
        )

        _LOGGER.info(
            "Established managed BLE connection to %s (RSSI=%s)",
            ble_device.address,
            getattr(ble_device, "rssi", "?"),
        )
        return client


async def ha_establish_connection(
    hass: HomeAssistant,
    address: str,
    name: str,
    *,
    disconnected_callback: Any = None,
    max_attempts: int = 4,
) -> BleakClientWithServiceCache:
    """Establish a managed BLE connection using HA's bluetooth stack.

    This function wraps bleak_retry_connector.establish_connection with:
    - Automatic stale connection cleanup
    - BLE device lookup via HA's bluetooth registry
    - Service caching for faster reconnections
    - Automatic retry with intelligent backoff
    - Scanning pause/resume (handled by HA's BleakClient wrapper)

    Args:
        hass: Home Assistant instance.
        address: BLE MAC address (e.g., "AA:BB:CC:DD:EE:FF").
        name: Device name for logging (e.g., "Tuya Mesh AA:BB:CC").
        disconnected_callback: Callback(client) when connection is lost.
        max_attempts: Maximum connection attempts (default 4).

    Returns:
        Connected BleakClientWithServiceCache instance.

    Raises:
        BleakError: If all connection attempts fail.
        TimeoutError: If connection times out.

    Example:
        client = await ha_establish_connection(
            hass, "AA:BB:CC:DD:EE:FF", "My Device",
            disconnected_callback=lambda c: print("Disconnected!"),
        )
        try:
            await client.write_gatt_char(uuid, data)
        finally:
            await client.disconnect()
    """
    from homeassistant.components.bluetooth import async_ble_device_from_address

    # Step 1: Clean up any stale connections (CRITICAL — prevents "Busy" errors)
    await close_stale_connections_by_address(address.upper())

    # Step 2: Get BLEDevice from HA's bluetooth registry
    ble_device = async_ble_device_from_address(hass, address.upper(), connectable=True)
    if ble_device is None:
        # Fall back to non-connectable (may work via proxy)
        ble_device = async_ble_device_from_address(
            hass, address.upper(), connectable=False
        )
    if ble_device is None:
        from tuya_ble_mesh.exceptions import MeshConnectionError

        msg = (
            f"Device {address} not found in HA bluetooth registry. "
            "Ensure device is powered on and in range of a BLE adapter or ESPHome proxy."
        )
        raise MeshConnectionError(msg)

    # Step 3: Establish connection with retries and caching
    # NOTE: HA's bluetooth wrapper (HaBleakClientWrapper) automatically:
    # - Pauses scanning during connection (prevents adapter "Busy" 0x0a)
    # - Resumes scanning after connection/disconnect
    # - Selects best adapter/proxy based on RSSI
    # - Manages connection slots to avoid exhaustion
    client = await establish_connection(
        BleakClientWithServiceCache,  # Use cached version for faster reconnects
        ble_device,
        name=name,
        disconnected_callback=disconnected_callback,
        max_attempts=max_attempts,
        use_services_cache=True,  # Enable GATT service caching
    )

    _LOGGER.info(
        "Established BLE connection to %s via HA bluetooth (RSSI=%s)",
        address,
        getattr(ble_device, "rssi", "?"),
    )
    return client


def parse_discovery_info(discovery_info: BluetoothServiceInfoBleak) -> dict[str, Any]:
    """Parse bluetooth discovery info into a dict for config flow.

    Extracts relevant fields from BluetoothServiceInfoBleak for use in
    config_flow.py discovery step.

    Args:
        discovery_info: Bluetooth service info from HA bluetooth integration.

    Returns:
        Dict with keys: address, name, rssi, service_uuids.
    """
    return {
        "address": discovery_info.address,
        "name": discovery_info.name or "",
        "rssi": getattr(discovery_info, "rssi", None),
        "service_uuids": getattr(discovery_info, "service_uuids", []),
    }
