"""BLE discovery and validation for Tuya BLE Mesh config flow.

Handles:
- Bluetooth discovery (async_step_bluetooth)
- Device validation and pairing (_validate_and_connect)
- Discovery confirmation (async_step_confirm)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

if TYPE_CHECKING:
    from homeassistant.data_entry_flow import FlowResult

from custom_components.tuya_ble_mesh.const import (
    CONF_DEVICE_TYPE,
    CONF_MESH_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_VENDOR_ID,
    DEFAULT_MESH_ADDRESS,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    SIG_MESH_PROV_UUID,
    SIG_MESH_PROXY_UUID,
)
from custom_components.tuya_ble_mesh.config_flow_telink import perform_telink_pairing

try:
    from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.scanner import mac_to_bytes
except ImportError:
    mac_to_bytes = None  # type: ignore[assignment]

_LOGGER = logging.getLogger(__name__)

def _rssi_to_signal_quality(rssi: int | None) -> str:
    """Convert RSSI dBm value to a human-readable signal quality label.

    Args:
        rssi: Signal strength in dBm (negative integer) or None if unknown.

    Returns:
        Human-readable label: Excellent, Good, Fair, Weak, or Unknown.
    """
    if rssi is None:
        return "Unknown"
    if rssi >= -65:
        return "Excellent"
    if rssi >= -75:
        return "Good"
    if rssi >= -85:
        return "Fair"
    return "Weak"

async def validate_and_connect(
    hass: Any,
    mac: str,
    device_type: str | None = None,
    mesh_name: str = "out_of_mesh",
    mesh_password: str = "123456",
) -> tuple[str, dict[str, Any]]:
    """Connect to device, detect type if needed, and verify basic communication.

    This method implements the Shelly-pattern: validate BEFORE creating config entry.

    Steps:
    1. BLE connect to device
    2. GATT service discovery to auto-detect device type (if not provided)
    3. Telink: PAIR_REQUEST → PAIR_SUCCESS (mesh login handshake)
    4. Send test command (status query) and verify RESPONSE with hex logging
    5. Return device_type + any discovered credentials/config

    PLAT-740 QC Round 3: Full implementation with pairing + verify + response check.
    Timeout: 30 seconds total for entire flow (connect + pair + verify).

    Args:
        hass: Home Assistant instance.
        mac: BLE MAC address.
        device_type: Known device type, or None to auto-detect.
        mesh_name: Telink mesh network name (for Telink devices).
        mesh_password: Telink mesh password (for Telink devices).

    Returns:
        Tuple of (detected_device_type, extra_data_dict).
        extra_data_dict may contain keys like net_key, dev_key, app_key for SIG devices.

    Raises:
        ValueError: With translatable error key if connection/pairing/verify fails.
        asyncio.TimeoutError: If total flow exceeds 30 seconds.
    """
    from homeassistant.components import bluetooth as ha_bluetooth

    _LOGGER.info("Validating device %s (type=%s)", mac, device_type or "auto-detect")

    # PLAT-740 AC6: 30s total timeout for entire flow
    async def _validate_inner() -> tuple[str, dict[str, Any]]:
        # Step 1: Check device is advertising
        ble_device = ha_bluetooth.async_ble_device_from_address(
            hass, mac.upper(), connectable=True
        )
        if ble_device is None:
            _LOGGER.warning("Device %s not found in HA bluetooth registry", mac)
            raise ValueError("device_not_found")

        # Step 2: Connect via Bleak
        from bleak import BleakClient
        from bleak_retry_connector import (
            BleakClientWithServiceCache,
            close_stale_connections_by_address,
            establish_connection,
        )

        await close_stale_connections_by_address(mac.upper())

        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                f"Validating {mac}",
                max_attempts=3,
                use_services_cache=True,
            )
        except Exception as exc:
            # PLAT-737: Detect BLE adapter busy (0x0a) errors
            exc_str = str(exc).lower()
            if "busy" in exc_str or "0x0a" in exc_str or "in progress" in exc_str:
                from custom_components.tuya_ble_mesh.repairs import async_create_issue_ble_adapter_busy

                _LOGGER.error(
                    "BLE adapter busy for %s — another integration is monopolizing the adapter. "
                    "User needs ESPHome Bluetooth Proxy or a second BLE adapter.",
                    mac,
                )
                # Create repair issue to guide user
                await async_create_issue_ble_adapter_busy(hass, f"Device {mac[-8:]}")
                raise ValueError("ble_adapter_busy") from exc
            _LOGGER.warning("BLE connect failed for %s: %s", mac, exc, exc_info=True)
            raise ValueError("cannot_connect_ble") from exc

        try:
            # Step 3: GATT service discovery to detect device type (if not provided)
            if device_type is None:
                services = client.services
                service_uuids = [str(s.uuid).lower() for s in services]

                _LOGGER.debug("Discovered services for %s: %s", mac, service_uuids)

                # SIG Mesh detection (0x1827 Provisioning or 0x1828 Proxy)
                if SIG_MESH_PROV_UUID in service_uuids or SIG_MESH_PROXY_UUID in service_uuids:
                    detected_type = DEVICE_TYPE_SIG_PLUG
                    _LOGGER.info("Auto-detected %s as SIG Mesh plug", mac)
                # Telink detection (00010203-... UUID prefix)
                elif any(uuid.startswith("00010203-0405-0607-0809-0a0b0c0d") for uuid in service_uuids):
                    detected_type = DEVICE_TYPE_LIGHT
                    _LOGGER.info("Auto-detected %s as Telink light", mac)
                else:
                    _LOGGER.warning("Could not auto-detect device type for %s (services=%s)", mac, service_uuids)
                    raise ValueError("unknown_device_type")
            else:
                detected_type = device_type

            # Step 4: Pairing/provisioning (device-type specific)
            extra_data: dict[str, Any] = {}

            if detected_type == DEVICE_TYPE_SIG_PLUG:
                # SIG Mesh: full provisioning handled by _run_provision
                # (we'll call that later, for now just verify it's a SIG device)
                services = client.services
                service_uuids = [str(s.uuid).lower() for s in services]
                if SIG_MESH_PROV_UUID not in service_uuids and SIG_MESH_PROXY_UUID not in service_uuids:
                    _LOGGER.warning("%s claims to be SIG plug but lacks SIG Mesh services", mac)
                    raise ValueError("device_type_mismatch")
                # Provisioning will be done in async_step_sig_plug (no change to existing flow)

            elif detected_type in (DEVICE_TYPE_LIGHT, DEVICE_TYPE_PLUG):
                # PLAT-740: Telink pairing — delegated to config_flow_telink
                extra_data = await perform_telink_pairing(client, mac, mesh_name, mesh_password, detected_type)

            else:
                # Unknown device type
                raise ValueError("unknown_device_type")

            _LOGGER.info("Device %s validated successfully (type=%s)", mac, detected_type)
            return detected_type, extra_data

        finally:
            await client.disconnect()

    # PLAT-740 AC6: Wrap entire flow in 30s timeout
    try:
        return await asyncio.wait_for(_validate_inner(), timeout=30.0)
    except asyncio.TimeoutError:
        _LOGGER.warning("Validation timed out for %s after 30s", mac)
        raise ValueError("timeout_validation")

