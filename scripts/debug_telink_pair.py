#!/usr/bin/env python3
"""Deep debug script for PLAT-733: Telink pairing + command debugging.

This script performs a complete Telink BLE mesh pairing sequence with
comprehensive hex-level logging of every BLE operation to identify why
commands don't work despite successful identification.

Based on PROTOCOL.md analysis and python-awox-mesh-light reference.
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from bleak import BleakClient, BleakScanner
from tuya_ble_mesh.const import (
    TELINK_CHAR_PAIRING,
    TELINK_CHAR_STATUS,
    TELINK_CHAR_COMMAND,
    TELINK_CMD_STATUS_QUERY,
    DP_TYPE_VALUE,
)
from tuya_ble_mesh.crypto import (
    generate_session_random,
    make_pair_packet,
    make_session_key,
)
from tuya_ble_mesh.protocol import (
    parse_pair_response,
    encode_command_packet,
    encode_compact_dp,
)
from tuya_ble_mesh.scanner import mac_to_bytes

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
_LOGGER = logging.getLogger(__name__)

# Device config (Malmbergs LED Driver)
DEVICE_MAC = "DC:23:4D:21:43:A5"
MESH_NAME = b"out_of_mesh"
MESH_PASSWORD = b"123456"
VENDOR_ID = b"\x01\x10"  # Malmbergs vendor ID (confirmed)

# DP IDs for Malmbergs (from PROTOCOL.md section 9)
DP_POWER = 121  # 0x79
DP_BRIGHTNESS = 122  # 0x7A


def hexdump(data: bytes, label: str = "Data") -> None:
    """Log hex dump of bytes."""
    hex_str = " ".join(f"{b:02X}" for b in data)
    _LOGGER.info(f"{label}: [{len(data)}B] {hex_str}")


async def scan_device(mac: str, timeout: float = 10.0) -> bool:
    """Scan for device and report RSSI."""
    _LOGGER.info(f"Scanning for {mac} (timeout={timeout}s)...")
    device = await BleakScanner.find_device_by_address(mac, timeout=timeout)
    if device is None:
        _LOGGER.error(f"Device {mac} not found in BLE scan")
        return False

    _LOGGER.info(f"Found: {device.name} at RSSI={device.rssi} dBm")
    return True


async def debug_pairing(client: BleakClient) -> tuple[bytes, bytes] | None:
    """Execute pairing with full hex logging.

    Returns (session_key, client_random) on success, None on failure.
    """
    _LOGGER.info("=" * 70)
    _LOGGER.info("STEP 1: Generate client random and build pair packet")
    _LOGGER.info("=" * 70)

    client_random = generate_session_random()
    hexdump(client_random, "Client random")

    pair_packet = make_pair_packet(MESH_NAME, MESH_PASSWORD, client_random)
    hexdump(pair_packet, "Pair packet (0x0C)")

    _LOGGER.info(f"Pair packet structure: [0x0C][8B random][8B encrypted proof]")
    _LOGGER.info(f"  Opcode: 0x{pair_packet[0]:02X}")
    _LOGGER.info(f"  Random: {pair_packet[1:9].hex()}")
    _LOGGER.info(f"  Encrypted: {pair_packet[9:17].hex()}")

    _LOGGER.info("=" * 70)
    _LOGGER.info("STEP 2: Write pair request to char 1914")
    _LOGGER.info("=" * 70)

    try:
        await asyncio.wait_for(
            client.write_gatt_char(TELINK_CHAR_PAIRING, pair_packet, response=True),
            timeout=10.0
        )
        _LOGGER.info("✓ Pair request write succeeded")
    except Exception as exc:
        _LOGGER.error(f"✗ Pair request write failed: {type(exc).__name__}: {exc}")
        return None

    _LOGGER.info("=" * 70)
    _LOGGER.info("STEP 3: Enable notifications on char 1911 (CRITICAL: BEFORE reading response)")
    _LOGGER.info("=" * 70)

    try:
        await asyncio.wait_for(
            client.write_gatt_char(TELINK_CHAR_STATUS, b"\x01", response=True),
            timeout=10.0
        )
        _LOGGER.info("✓ Notification enable succeeded")
    except Exception as exc:
        _LOGGER.error(f"✗ Notification enable failed: {type(exc).__name__}: {exc}")
        return None

    _LOGGER.info("=" * 70)
    _LOGGER.info("STEP 4: Read pair response from char 1914")
    _LOGGER.info("=" * 70)

    try:
        response_data = bytes(
            await asyncio.wait_for(client.read_gatt_char(TELINK_CHAR_PAIRING), timeout=10.0)
        )
        hexdump(response_data, "Pair response")
    except Exception as exc:
        _LOGGER.error(f"✗ Pair response read failed: {type(exc).__name__}: {exc}")
        return None

    try:
        response = parse_pair_response(response_data)
        _LOGGER.info(f"Response opcode: 0x{response.opcode:02X}")

        if response.opcode == 0x0D:  # SUCCESS
            _LOGGER.info("✓ Pairing SUCCESS (opcode 0x0D)")
            hexdump(response.device_random, "Device random")
        elif response.opcode == 0x0E:  # FAILURE
            _LOGGER.error("✗ Pairing FAILURE (opcode 0x0E) — wrong credentials?")
            return None
        else:
            _LOGGER.error(f"✗ Unexpected opcode: 0x{response.opcode:02X}")
            return None
    except Exception as exc:
        _LOGGER.error(f"✗ Failed to parse response: {type(exc).__name__}: {exc}")
        return None

    _LOGGER.info("=" * 70)
    _LOGGER.info("STEP 5: Derive session key")
    _LOGGER.info("=" * 70)

    device_random = response.device_random
    session_key = make_session_key(MESH_NAME, MESH_PASSWORD, client_random, device_random)
    hexdump(session_key, "Session key [SENSITIVE]")
    _LOGGER.info("✓ Session key derived successfully")

    return session_key, client_random


async def debug_command(
    client: BleakClient,
    session_key: bytes,
    mac_bytes: bytes,
    sequence: int,
    opcode: int,
    params: bytes,
    label: str,
) -> bool:
    """Send a command with full hex logging.

    Returns True on success, False on failure.
    """
    _LOGGER.info("=" * 70)
    _LOGGER.info(f"COMMAND: {label}")
    _LOGGER.info("=" * 70)

    packet = encode_command_packet(
        session_key,
        mac_bytes,
        sequence,
        0xFFFF,  # broadcast
        opcode,
        params,
        vendor_id=VENDOR_ID,
    )

    hexdump(packet, f"Command packet (opcode 0x{opcode:02X})")
    _LOGGER.info(f"Packet structure: [3B seq][2B checksum][15B encrypted]")
    _LOGGER.info(f"  Sequence: {int.from_bytes(packet[0:3], 'little')}")
    _LOGGER.info(f"  Checksum: {packet[3:5].hex()}")
    _LOGGER.info(f"  Encrypted: {packet[5:20].hex()}")
    _LOGGER.info(f"  Dest ID: 0xFFFF (broadcast)")
    _LOGGER.info(f"  Opcode: 0x{opcode:02X}")
    hexdump(params, "  Params")

    try:
        await asyncio.wait_for(
            client.write_gatt_char(TELINK_CHAR_COMMAND, packet, response=False),
            timeout=5.0
        )
        _LOGGER.info(f"✓ Command write succeeded")
        return True
    except Exception as exc:
        _LOGGER.error(f"✗ Command write failed: {type(exc).__name__}: {exc}")
        return False


async def main():
    """Main debug flow."""
    _LOGGER.info("╔" + "=" * 68 + "╗")
    _LOGGER.info("║  PLAT-733: Telink BLE Mesh Deep Debug Script                     ║")
    _LOGGER.info("║  Device: Malmbergs LED Driver 9952126                            ║")
    _LOGGER.info("╚" + "=" * 68 + "╝")

    # Phase 1: Scan
    if not await scan_device(DEVICE_MAC):
        return 1

    await asyncio.sleep(1)

    # Phase 2: Connect
    _LOGGER.info("=" * 70)
    _LOGGER.info("CONNECTING TO DEVICE")
    _LOGGER.info("=" * 70)

    device = await BleakScanner.find_device_by_address(DEVICE_MAC, timeout=10.0)
    if device is None:
        _LOGGER.error("Device not found in second scan")
        return 1

    async with BleakClient(device) as client:
        _LOGGER.info(f"✓ BLE connected to {DEVICE_MAC}")
        _LOGGER.info(f"  MTU: {client.mtu_size}")
        _LOGGER.info(f"  Connected: {client.is_connected}")

        # Phase 3: Pairing
        result = await debug_pairing(client)
        if result is None:
            _LOGGER.error("Pairing failed — stopping here")
            return 1

        session_key, client_random = result
        mac_bytes = mac_to_bytes(DEVICE_MAC)

        # Give device time to settle
        await asyncio.sleep(2)

        # Phase 4: Test commands
        sequence = 0

        # Command 1: Status query (0xDA)
        _LOGGER.info("\n")
        params_status = b"\x10"  # Status query param
        success = await debug_command(
            client, session_key, mac_bytes, sequence, TELINK_CMD_STATUS_QUERY,
            params_status, "Status Query (0xDA)"
        )
        if not success:
            _LOGGER.warning("Status query failed — continuing anyway")

        sequence += 1
        await asyncio.sleep(1)

        # Command 2: Power ON via compact DP (0xD2)
        _LOGGER.info("\n")
        params_on = encode_compact_dp(DP_POWER, DP_TYPE_VALUE, 1)
        hexdump(params_on, "Compact DP for power ON")
        _LOGGER.info(f"  DP structure: [dp_id=0x{DP_POWER:02X}][type=0x{DP_TYPE_VALUE:02X}][len][value=1]")

        success = await debug_command(
            client, session_key, mac_bytes, sequence, 0xD2,
            params_on, "Power ON (0xD2 compact DP)"
        )
        if not success:
            _LOGGER.error("Power ON command failed")
        else:
            _LOGGER.info("⚡ Power ON command sent — check if LED responds!")

        sequence += 1
        await asyncio.sleep(3)

        # Command 3: Power OFF via compact DP (0xD2)
        _LOGGER.info("\n")
        params_off = encode_compact_dp(DP_POWER, DP_TYPE_VALUE, 0)
        hexdump(params_off, "Compact DP for power OFF")

        success = await debug_command(
            client, session_key, mac_bytes, sequence, 0xD2,
            params_off, "Power OFF (0xD2 compact DP)"
        )
        if not success:
            _LOGGER.error("Power OFF command failed")
        else:
            _LOGGER.info("⚡ Power OFF command sent — check if LED responds!")

        sequence += 1
        await asyncio.sleep(3)

        # Command 4: Brightness 50% via compact DP (0xD2)
        _LOGGER.info("\n")
        params_bright = encode_compact_dp(DP_BRIGHTNESS, DP_TYPE_VALUE, 50)
        hexdump(params_bright, "Compact DP for brightness 50%")

        success = await debug_command(
            client, session_key, mac_bytes, sequence, 0xD2,
            params_bright, "Brightness 50% (0xD2 compact DP)"
        )
        if not success:
            _LOGGER.error("Brightness command failed")
        else:
            _LOGGER.info("⚡ Brightness command sent — check if LED responds!")

        await asyncio.sleep(3)

        _LOGGER.info("\n")
        _LOGGER.info("=" * 70)
        _LOGGER.info("DEBUG SESSION COMPLETE")
        _LOGGER.info("=" * 70)
        _LOGGER.info("OBSERVATIONS:")
        _LOGGER.info("  1. Did pairing succeed? (Check for 0x0D response)")
        _LOGGER.info("  2. Did commands write successfully to char 1912?")
        _LOGGER.info("  3. Did the LED physically respond to ON/OFF/brightness?")
        _LOGGER.info("  4. Check the hex dumps for any anomalies in packet structure")
        _LOGGER.info("=" * 70)

    return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        _LOGGER.info("Interrupted by user")
        sys.exit(130)
    except Exception as exc:
        _LOGGER.error(f"Fatal error: {type(exc).__name__}: {exc}", exc_info=True)
        sys.exit(1)
