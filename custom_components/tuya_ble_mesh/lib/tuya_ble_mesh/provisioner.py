"""Telink BLE Mesh provisioner — 4-step pairing handshake.

Handles the complete provisioning flow:
1. Send pair request (random + encrypted proof) to char 1914
2. Enable notifications on char 1911 (write 0x01) — BEFORE reading response!
3. Read pair response (device random or failure) from char 1914
4. Derive session key, optionally set mesh credentials via char 1914

CRITICAL: Step 2 must happen BEFORE step 3, as per PROTOCOL.md section 10.
The device requires the notification enable write before it will respond.

After provisioning, the device accepts encrypted commands on char 1912
and sends encrypted status on char 1911.

SECURITY: Session keys and mesh credentials are NEVER logged.
Only operation names, lengths, and success/failure status are safe to log.
"""

from __future__ import annotations

import asyncio
import logging

from bleak import BleakClient

from tuya_ble_mesh.const import (
    PAIR_OPCODE_FAILURE,
    PAIR_OPCODE_SET_NAME,
    PAIR_OPCODE_SET_OK,
    PAIR_OPCODE_SET_PASS,
    PAIR_OPCODE_SUCCESS,
    TELINK_CHAR_PAIRING,
    TELINK_CHAR_STATUS,
)
from tuya_ble_mesh.crypto import (
    encrypt_mesh_credential,
    generate_session_random,
    make_pair_packet,
    make_session_key,
)
from tuya_ble_mesh.exceptions import ProvisioningError
from tuya_ble_mesh.protocol import parse_pair_response

_LOGGER = logging.getLogger(__name__)

# Timeout for individual GATT read/write operations during provisioning
_GATT_TIMEOUT = 10.0


async def pair(
    client: BleakClient,
    mesh_name: bytes,
    mesh_password: bytes,
) -> tuple[bytes, bytes]:
    """Execute the 3-step pairing handshake.

    Step 1: Write pair request to characteristic 1914.
    Step 2: Enable notifications on 1911 (write 0x01) — BEFORE reading pair response!
    Step 3: Read response — success (0x0D + device random) or failure (0x0E).
    Step 4: Derive session key from both randoms.

    CRITICAL: The notification enable (0x01 to char 1911) must happen BEFORE
    reading the pair response from char 1914, as per PROTOCOL.md section 10.

    Args:
        client: Connected BleakClient.
        mesh_name: Mesh network name (e.g. b"out_of_mesh").
        mesh_password: Mesh network password (e.g. b"123456").

    Returns:
        Tuple of (session_key, client_random).

    Raises:
        ProvisioningError: If pairing fails at any step.
    """
    _LOGGER.info("Starting pairing handshake")

    # Step 1: Generate random and build pair packet
    client_random = generate_session_random()
    pair_packet = make_pair_packet(mesh_name, mesh_password, client_random)

    _LOGGER.debug(
        "Step 1/4: Writing PAIR_REQUEST (0x0C, %d bytes) to %s",
        len(pair_packet),
        TELINK_CHAR_PAIRING,
    )
    await asyncio.wait_for(
        client.write_gatt_char(TELINK_CHAR_PAIRING, pair_packet, response=True),
        timeout=_GATT_TIMEOUT,
    )
    _LOGGER.debug("Step 1/4: PAIR_REQUEST written OK")

    # Step 2: Enable notifications on char 1911 BEFORE reading pair response
    # This is CRITICAL per PROTOCOL.md section 10 — the device requires this
    # write before it will respond to the pair request.
    _LOGGER.debug(
        "Step 2/4: Enabling notifications on %s (before reading pair response)",
        TELINK_CHAR_STATUS,
    )
    await asyncio.wait_for(
        client.write_gatt_char(TELINK_CHAR_STATUS, b"\x01", response=True),
        timeout=_GATT_TIMEOUT,
    )
    _LOGGER.debug("Step 2/4: Notifications enabled OK")

    # Step 3: Read pair response
    _LOGGER.debug("Step 3/4: Reading pair response from %s", TELINK_CHAR_PAIRING)
    response_data = bytes(
        await asyncio.wait_for(client.read_gatt_char(TELINK_CHAR_PAIRING), timeout=_GATT_TIMEOUT)
    )
    _LOGGER.debug(
        "Step 3/4: Got %d bytes, opcode = 0x%02X",
        len(response_data),
        response_data[0] if response_data else 0,
    )

    response = parse_pair_response(response_data)

    if response.opcode == PAIR_OPCODE_FAILURE:
        msg = "Device rejected pairing (0x0E — wrong mesh name or password)"
        raise ProvisioningError(msg)

    if response.opcode != PAIR_OPCODE_SUCCESS:
        msg = f"Unexpected pair response opcode: 0x{response.opcode:02X}"
        raise ProvisioningError(msg)

    # Step 4: Derive session key
    device_random = response.device_random
    session_key = make_session_key(mesh_name, mesh_password, client_random, device_random)

    _LOGGER.info(
        "Pairing handshake complete: 0x0C → 0x0D, session key derived (%d bytes) [REDACTED]",
        len(session_key),
    )
    return session_key, client_random


async def set_mesh_credentials(
    client: BleakClient,
    session_key: bytes,
    new_name: bytes,
    new_password: bytes,
) -> None:
    """Set new mesh credentials on a paired device.

    Writes encrypted name and password to characteristic 1914,
    then reads confirmation.

    Args:
        client: Connected and paired BleakClient.
        session_key: 16-byte session key from pair().
        new_name: New mesh network name.
        new_password: New mesh network password.

    Raises:
        ProvisioningError: If credential setting fails.
    """
    _LOGGER.info("Setting mesh credentials (SET_NAME 0x04 + SET_PASS 0x05)")

    # Encrypt and write name
    enc_name = encrypt_mesh_credential(session_key, new_name)
    name_packet = bytes([PAIR_OPCODE_SET_NAME]) + enc_name
    _LOGGER.debug(
        "Writing SET_MESH_NAME (0x04, %d bytes) to %s",
        len(name_packet),
        TELINK_CHAR_PAIRING,
    )
    await asyncio.wait_for(
        client.write_gatt_char(TELINK_CHAR_PAIRING, name_packet, response=True),
        timeout=_GATT_TIMEOUT,
    )
    _LOGGER.debug("SET_MESH_NAME written OK")

    # Encrypt and write password
    enc_pass = encrypt_mesh_credential(session_key, new_password)
    pass_packet = bytes([PAIR_OPCODE_SET_PASS]) + enc_pass
    _LOGGER.debug(
        "Writing SET_MESH_PASSWORD (0x05, %d bytes) to %s",
        len(pass_packet),
        TELINK_CHAR_PAIRING,
    )
    await asyncio.wait_for(
        client.write_gatt_char(TELINK_CHAR_PAIRING, pass_packet, response=True),
        timeout=_GATT_TIMEOUT,
    )
    _LOGGER.debug("SET_MESH_PASSWORD written OK")

    # Read confirmation
    _LOGGER.debug("Reading credential confirmation from %s", TELINK_CHAR_PAIRING)
    confirm_data = bytes(
        await asyncio.wait_for(client.read_gatt_char(TELINK_CHAR_PAIRING), timeout=_GATT_TIMEOUT)
    )
    confirm = parse_pair_response(confirm_data)
    _LOGGER.debug("Credential confirmation opcode: 0x%02X", confirm.opcode)

    if confirm.opcode != PAIR_OPCODE_SET_OK:
        msg = f"Credential set failed, expected SET_OK (0x07), got 0x{confirm.opcode:02X}"
        raise ProvisioningError(msg)

    _LOGGER.info("Mesh credentials set successfully (SET_OK 0x07 confirmed)")


async def enable_notifications(client: BleakClient) -> None:
    """Enable notifications on the status characteristic (1911).

    Writes 0x01 to the status characteristic to enable unsolicited
    status notifications from the device.

    NOTE: Telink BLE mesh devices do NOT support standard CCCD-based
    notification subscription (bleak's start_notify). Calling start_notify
    triggers an EOFError on the BlueZ D-Bus connection, which kills the
    entire BleakClient. Confirmed on BlueZ 5.82 and 5.83.

    Args:
        client: Connected and paired BleakClient.
    """
    _LOGGER.debug("Enabling notifications on %s", TELINK_CHAR_STATUS)
    await asyncio.wait_for(
        client.write_gatt_char(TELINK_CHAR_STATUS, b"\x01", response=True),
        timeout=_GATT_TIMEOUT,
    )
    _LOGGER.info("Notifications enabled on status characteristic")


async def provision(
    client: BleakClient,
    current_name: bytes,
    current_password: bytes,
    new_name: bytes | None = None,
    new_password: bytes | None = None,
) -> bytes:
    """Complete provisioning: pair, optionally set credentials.

    NOTE: enable_notifications() is now called inside pair() before reading
    the pair response, as required by the Telink protocol (PROTOCOL.md section 10).

    Args:
        client: Connected BleakClient.
        current_name: Current mesh name (e.g. b"out_of_mesh").
        current_password: Current mesh password (e.g. b"123456").
        new_name: New mesh name (optional, keeps current if None).
        new_password: New mesh password (optional, keeps current if None).

    Returns:
        16-byte session key for encrypted communication.

    Raises:
        ProvisioningError: If any step fails.
    """
    _LOGGER.info(
        "Provisioning: pair handshake + %s (notifications in pair)",
        "set credentials" if (new_name or new_password) else "skip credentials",
    )

    session_key, _ = await pair(client, current_name, current_password)

    if new_name is not None or new_password is not None:
        name = new_name if new_name is not None else current_name
        password = new_password if new_password is not None else current_password
        await set_mesh_credentials(client, session_key, name, password)
    else:
        _LOGGER.warning(
            "Skipping set_mesh_credentials — device will stay in "
            "pairing mode! Pass new_name/new_password to provision.",
        )

    # Notifications are already enabled in pair() — do NOT call enable_notifications() again

    _LOGGER.info("Provisioning complete — device ready for encrypted commands")
    return session_key
