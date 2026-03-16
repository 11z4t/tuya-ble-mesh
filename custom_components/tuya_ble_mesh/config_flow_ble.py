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
                # PLAT-740: Telink pairing — PAIR_REQUEST → PAIR_SUCCESS
                services = client.services
                service_uuids = [str(s.uuid).lower() for s in services]
                has_telink = any(uuid.startswith("00010203-0405-0607-0809-0a0b0c0d") for uuid in service_uuids)
                if not has_telink:
                    _LOGGER.warning("%s claims to be Telink but lacks Telink GATT service", mac)
                    raise ValueError("device_type_mismatch")

                # Import provisioner for Telink pairing
                from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.provisioner import pair

                _LOGGER.info("Starting Telink mesh pairing for %s", mac)
                try:
                    session_key, _ = await pair(client, mesh_name.encode("utf-8"), mesh_password.encode("utf-8"))
                    _LOGGER.info("Telink pairing succeeded for %s (session_key=%d bytes)", mac, len(session_key))
                except Exception as exc:
                    _LOGGER.warning("Telink pairing failed for %s: %s", mac, exc, exc_info=True)
                    # Map provisioning errors to user-friendly keys
                    from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.exceptions import ProvisioningError
                    if isinstance(exc, ProvisioningError):
                        raise ValueError("pairing_failed")
                    raise ValueError("pairing_failed") from exc

                # PLAT-740 QC BRIST 2: Verify — send status query and VALIDATE RESPONSE
                _LOGGER.info("Verifying Telink device %s with status query (0xE0)", mac)
                from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.const import TELINK_CHAR_COMMAND, TELINK_CHAR_NOTIFY, TELINK_CMD_STATUS_QUERY
                from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.protocol import encode_command_packet

                # Build status query command (0xE0)
                status_query = encode_command_packet(
                    TELINK_CMD_STATUS_QUERY,
                    b"\x10",  # Status query param
                    session_key,
                    0,  # sequence (first command after pairing)
                    0,  # mesh_id (default)
                    mac_to_bytes(mac),
                )

                # QC REQUIREMENT: Hex-log request
                _LOGGER.warning(
                    "VERIFY TX [%s] → 0xE0 status query: %s",
                    mac,
                    status_query.hex() if isinstance(status_query, bytes) else status_query,
                )

                # Subscribe to notifications BEFORE sending command
                response_received = asyncio.Event()
                response_data: list[bytes] = []

                def notification_handler(sender: Any, data: bytes) -> None:
                    """Capture response from device."""
                    _LOGGER.warning("VERIFY RX [%s] ← notification: %s", mac, data.hex())
                    response_data.append(data)
                    response_received.set()

                await client.start_notify(TELINK_CHAR_NOTIFY, notification_handler)

                try:
                    # Send command
                    await client.write_gatt_char(TELINK_CHAR_COMMAND, status_query, response=True)
                    _LOGGER.info("Status query (0xE0) sent to %s, waiting for response...", mac)

                    # Wait up to 5 seconds for response
                    try:
                        await asyncio.wait_for(response_received.wait(), timeout=5.0)
                        if response_data:
                            _LOGGER.info(
                                "Device %s responded to verify command — device verified (response: %s)",
                                mac,
                                response_data[0].hex()[:40],
                            )
                        else:
                            _LOGGER.warning("Device %s: response event set but no data captured", mac)
                            raise ValueError("verify_failed")
                    except asyncio.TimeoutError:
                        _LOGGER.warning("Device %s did not respond to verify command within 5s", mac)
                        raise ValueError("verify_failed")
                finally:
                    await client.stop_notify(TELINK_CHAR_NOTIFY)

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


async def async_step_bluetooth(
    flow: Any,
    discovery_info: BluetoothServiceInfoBleak,
) -> FlowResult:
    """Handle bluetooth discovery.

    Args:
        flow: Config flow instance.
        discovery_info: Bluetooth service info from HA bluetooth integration.

    Returns:
        Flow result dict.
    """
    from homeassistant.components.bluetooth import async_ble_device_from_address

    address: str = discovery_info.address
    name: str = discovery_info.name or ""

    _LOGGER.info("Bluetooth discovery: %s (%s)", name, address)

    # Check if already configured
    await flow.async_set_unique_id(address)
    # If device already has a config entry, signal reconnect and abort discovery
    flow._abort_if_unique_id_configured()

    # DEBUG: Log exactly what the device advertises
    _LOGGER.warning(
        "BLE Discovery: name=%s addr=%s uuids=%s rssi=%s",
        name, address, getattr(discovery_info, "service_uuids", []),
        getattr(discovery_info, "rssi", None),
    )

    # PLAT-736: Discovery logic — only match devices in pairing mode
    # - out_of_mesh* name → ALWAYS in pairing mode (accept both 0x1827 and 0x1828)
    # - tymesh* name → already paired Telink device, NOT in pairing mode (reject)
    # - Other names + 0x1827 (Provisioning) → in pairing mode
    # - Other names + ONLY 0x1828 (Proxy) → already provisioned (reject)
    # PLAT-694: After partial provisioning or device removal, plug keeps blinking
    # and may advertise out_of_mesh* + 0x1828. We must accept this for re-discovery.
    service_uuids = getattr(discovery_info, "service_uuids", [])

    # PLAT-736: Reject already-paired Telink mesh devices
    if name.startswith("tymesh"):
        _LOGGER.debug(
            "Ignoring discovery for %s (already paired Telink mesh device: name=%s)",
            address,
            name,
        )
        return flow.async_abort(reason="not_in_pairing_mode")

    # PLAT-731 / PLAT-739: S17* devices are Malmbergs SIG Mesh plugs
    # Skip UUID validation for S17* - they are SIG Mesh by name pattern
    is_s17_plug = name.startswith("S17")

    if not is_s17_plug and not name.startswith("out_of_mesh"):
        # Device name does not indicate pairing mode — check service UUIDs
        has_prov = SIG_MESH_PROV_UUID in service_uuids
        has_proxy = SIG_MESH_PROXY_UUID in service_uuids
        if not has_prov and has_proxy:
            # Only Proxy Service (no Provisioning) → already paired device, not pairing mode
            _LOGGER.debug(
                "Ignoring discovery for %s (already provisioned: name=%s, services=%s)",
                address,
                name,
                service_uuids,
            )
            return flow.async_abort(reason="not_in_pairing_mode")
        if not has_prov and not has_proxy:
            # No SIG Mesh services at all — not a SIG Mesh device in pairing mode
            _LOGGER.debug(
                "Ignoring discovery for %s (not in pairing mode: name=%s, services=%s)",
                address,
                name,
                service_uuids,
            )
            return flow.async_abort(reason="not_in_pairing_mode")

    #  Check if device is still advertising (stale flow protection)
    # If the device is not currently available in HA's bluetooth stack, ignore the discovery.
    # This prevents stale discovery flows from persisting after a device stops advertising.
    # PLAT-737: Use connectable=True to signal connection intent to HA bluetooth manager
    try:
        ble_device = async_ble_device_from_address(flow.hass, address, connectable=True)
        if ble_device is None:
            _LOGGER.debug(
                "Ignoring stale discovery for %s (device no longer advertising)", address
            )
            return flow.async_abort(reason="device_not_available")
    except RuntimeError:
        # BluetoothManager not initialized (e.g. in tests) -- skip stale check
        _LOGGER.debug("BluetoothManager not available, skipping stale check for %s", address)

    # Detect human-readable device category from service UUIDs
    # PLAT-694: Accept both Provisioning (0x1827) and Proxy (0x1828) services
    # PLAT-739: S17* devices are SIG Mesh plugs identified by name, not UUID
    is_sig_mesh = (
        is_s17_plug
        or SIG_MESH_PROV_UUID in service_uuids
        or SIG_MESH_PROXY_UUID in service_uuids
    )
    device_category = "Smart Plug" if is_sig_mesh else "LED Light"
    rssi = getattr(discovery_info, "rssi", None)

    #  Auto-detect device type based on service UUIDs or name pattern
    auto_device_type = None

    if is_s17_plug:
        # PLAT-739: S17* devices are always SIG Mesh plugs
        auto_device_type = DEVICE_TYPE_SIG_PLUG
        _LOGGER.info("SIG Mesh plug detected via S17* name pattern: %s (%s)", name, address)
    elif is_sig_mesh:  # Match both Provisioning (0x1827) and Proxy (0x1828)
        # SIG Mesh device -> Plug
        auto_device_type = DEVICE_TYPE_SIG_PLUG
    elif any(
        uuid.startswith("00010203-0405-0607-0809-0a0b0c0d") for uuid in service_uuids
    ):
        # Telink mesh UUID prefix -> Light
        auto_device_type = DEVICE_TYPE_LIGHT

    flow._discovery_info = {
        "address": address,
        "name": name,
        "rssi": rssi,
        "device_category": device_category,
        "auto_device_type": auto_device_type,
    }

    # PLAT-660 / PLAT-693: Set title_placeholders for discovery card
    # Show device category + MAC so users know what type of device was found
    # PLAT-693 fix: Include device_category in title to show device type clearly
    short_mac = address[-8:]
    display_title = f"{device_category} {short_mac}"
    flow.context["title_placeholders"] = {
        "name": display_title,
        "category": device_category,
        "mac": short_mac,
    }

    # Auto-detect SIG Mesh devices by service UUID.
    # 0x1827 = Provisioning Service (unprovisioned device)
    # 0x1828 = Proxy Service (already provisioned)
    # PLAT-694: Accept both — device may advertise 0x1828 after partial provisioning
    if auto_device_type == DEVICE_TYPE_SIG_PLUG and is_sig_mesh:
        _LOGGER.info("SIG Mesh device in pairing mode: %s", address)
        # Delegate to SIG plug flow (will be imported from config_flow_sig)
        from custom_components.tuya_ble_mesh.config_flow_sig import async_step_sig_plug
        return await async_step_sig_plug(flow, None)

    # Delegate to confirm step
    # Import to avoid circular dependency
    return await async_step_confirm_impl(flow, None)


async def async_step_confirm_impl(flow: Any, user_input: dict[str, Any] | None) -> FlowResult:
    """Confirm bluetooth discovery and choose device type.

    PLAT-659: Discovery NEVER auto-creates entities. The user must explicitly
    confirm the device via this form. Auto-detection only pre-fills the device
    type default for convenience. This matches Shelly's behaviour where discovery
    proposes integration but never creates entities without user action.

    PLAT-740: Connect + validate BEFORE creating config entry (Shelly pattern).

    Args:
        flow: Config flow instance.
        user_input: User-provided configuration data.

    Returns:
        Flow result dict.
    """
    import voluptuous as vol

    errors: dict[str, str] = {}

    # Use auto-detected device type as default if available
    default_device_type = DEVICE_TYPE_LIGHT
    if flow._discovery_info:
        auto_type = flow._discovery_info.get("auto_device_type")
        if auto_type in (DEVICE_TYPE_LIGHT, DEVICE_TYPE_PLUG):
            default_device_type = auto_type

    # PLAT-740: User submitted — validate BEFORE creating entry
    if user_input is not None and flow._discovery_info:
        # Validate vendor_id if provided
        from custom_components.tuya_ble_mesh.config_flow_validators import _validate_vendor_id
        vendor_id_str = user_input.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID)
        vendor_id_error = _validate_vendor_id(str(vendor_id_str))
        if vendor_id_error:
            errors[CONF_VENDOR_ID] = vendor_id_error

        if not errors:
            mac = flow._discovery_info["address"]
            device_type = user_input.get(CONF_DEVICE_TYPE, default_device_type)
            mesh_name = user_input.get(CONF_MESH_NAME, "out_of_mesh")
            mesh_password = user_input.get(CONF_MESH_PASSWORD, "123456")
            mesh_address = user_input.get(CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS)

            # PLAT-740: CRITICAL — Connect and validate BEFORE creating entry
            try:
                validated_type, extra_data = await validate_and_connect(
                    flow.hass, mac, device_type, mesh_name, mesh_password
                )
                # Update device_type with validated type (in case auto-detected)
                device_type = validated_type
            except ValueError as exc:
                # Map exceptions to user-friendly error keys
                error_key = str(exc).strip("'\"")
                errors["base"] = error_key
            except Exception as exc:
                _LOGGER.warning("Validation failed for %s: %s", mac, exc, exc_info=True)
                errors["base"] = "cannot_connect_ble"

        if not errors:
            await flow.async_set_unique_id(mac)
            flow._abort_if_unique_id_configured()

            return flow._finalize_entry(
                mac=mac,
                device_type=device_type,
                mesh_name=mesh_name,
                mesh_password=mesh_password,
                vendor_id=vendor_id_str,
                mesh_address=mesh_address,
            )

    # UX: If device type was auto-detected, skip the dropdown
    auto_detected = flow._discovery_info and flow._discovery_info.get("auto_device_type")
    confirm_schema: dict[object, object] = {}
    if not auto_detected:
        confirm_schema[vol.Required(CONF_DEVICE_TYPE, default=default_device_type)] = vol.In(
            {DEVICE_TYPE_LIGHT: "Light", DEVICE_TYPE_PLUG: "Plug"}
        )
    if flow.show_advanced_options:
        confirm_schema[vol.Optional(CONF_MESH_NAME, default="out_of_mesh")] = str
        confirm_schema[vol.Optional(CONF_MESH_PASSWORD, default="123456")] = str
        confirm_schema[vol.Optional(CONF_VENDOR_ID, default=DEFAULT_VENDOR_ID)] = str
        confirm_schema[vol.Optional(CONF_MESH_ADDRESS, default=DEFAULT_MESH_ADDRESS)] = int

    rssi_raw = flow._discovery_info.get("rssi") if flow._discovery_info else None
    rssi_int = int(rssi_raw) if rssi_raw is not None else None
    return flow.async_show_form(
        step_id="confirm",
        data_schema=vol.Schema(confirm_schema),
        description_placeholders={
            "name": (
                flow._discovery_info.get("name", "Unknown")
                if flow._discovery_info
                else "Unknown"
            ),
            # Human-readable signal quality label (used in current strings.json)
            "signal_quality": _rssi_to_signal_quality(rssi_int),
            "category": (
                flow._discovery_info.get("device_category", "Smart Device")
                if flow._discovery_info
                else "Smart Device"
            ),
            # Legacy placeholders kept for older translated strings that reference them
            "rssi": str(rssi_raw) if rssi_raw is not None else "?",
            "mac": (
                flow._discovery_info.get("address", "") if flow._discovery_info else ""
            ),
        },
        errors=errors,
    )
