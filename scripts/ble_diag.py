#!/usr/bin/env python3
"""BLE Command Protocol Diagnostic — hex-dump entire flow.

Temporary diagnostic script. Tests:
1. Connect + pair (3-step AES handshake)
2. Char 1911 raw read + write 0x01
3. Command: power ON with dest_id=0
4. Command: power ON with dest_id=0xFFFF
5. Command: power OFF with dest_id=0
6. Command: power ON with random sequence
7. start_notify (LAST — known to kill BlueZ D-Bus connection)

SECURITY: Session keys and credentials are NEVER logged.
Only hex of encrypted packets (safe) and lengths are shown.
"""

import argparse
import asyncio
import contextlib
import logging
import os
import pathlib
import sys

from bleak import BleakClient, BleakScanner

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))

from tuya_ble_mesh.const import (
    TARGET_DEVICE_MAC,
    TELINK_CHAR_COMMAND,
    TELINK_CHAR_STATUS,
    TELINK_CMD_POWER,
)
from tuya_ble_mesh.crypto import crypt_payload, make_checksum
from tuya_ble_mesh.protocol import build_nonce, encode_command_payload
from tuya_ble_mesh.provisioner import pair

_LOGGER = logging.getLogger(__name__)

_DEFAULT_CONNECT_RETRIES = 5


async def _connect_with_retry(
    address: str,
    timeout: float,
    max_retries: int,
) -> BleakClient:
    """Connect with retry logic matching device.py pattern."""
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            ble_device = await BleakScanner.find_device_by_address(address, timeout=timeout)
            if ble_device is None:
                print(f"  Device {address} not found in scan")
                continue

            client = BleakClient(ble_device, timeout=timeout)
            await client.connect()
            print(f"  Connected on attempt {attempt}/{max_retries}")
            return client
        except Exception as exc:
            last_exc = exc
            backoff = min(2.0 * attempt, 8.0)
            print(
                f"  Attempt {attempt}/{max_retries} failed: "
                f"{type(exc).__name__}, retrying in {backoff:.1f}s"
            )
            # Clear stale BlueZ state
            with contextlib.suppress(Exception):
                proc = await asyncio.create_subprocess_exec(
                    "bluetoothctl",
                    "remove",
                    address,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            await asyncio.sleep(backoff)

    msg = f"Failed to connect after {max_retries} attempts"
    raise ConnectionError(msg) from last_exc


def _hex(data: bytes) -> str:
    """Format bytes as hex string."""
    return data.hex(" ")


def _build_command(
    key: bytes,
    mac_bytes: bytes,
    sequence: int,
    dest_id: int,
    opcode: int,
    params: bytes,
) -> bytes:
    """Build a 20-byte encrypted command packet (diagnostic version)."""
    seq_bytes = sequence.to_bytes(3, "little")
    nonce = build_nonce(mac_bytes, sequence)
    payload = encode_command_payload(dest_id, opcode, params)
    checksum = make_checksum(key, nonce, payload)
    encrypted = crypt_payload(key, nonce, payload)
    return seq_bytes + checksum[:2] + encrypted


async def _send_and_observe(
    client: BleakClient,
    label: str,
    packet: bytes,
    wait: float = 3.0,
) -> None:
    """Send a command packet and wait, observing lamp visually."""
    print(f"  Packet: {_hex(packet)}")
    await client.write_gatt_char(TELINK_CHAR_COMMAND, packet, response=False)
    print(f"  Sent. Observe lamp for {wait:.0f}s...")
    await asyncio.sleep(wait)


async def run_diagnostics(mac: str, timeout: float) -> None:
    """Run the full diagnostic flow."""
    mesh_name = b"out_of_mesh"
    mesh_password = b"123456"

    print(f"\n{'=' * 60}")
    print("  BLE Command Protocol Diagnostic")
    print(f"  Target: {mac}")
    print(f"{'=' * 60}\n")

    # Step 1: Connect
    print("[1] Connecting...")
    client = await _connect_with_retry(mac, timeout, _DEFAULT_CONNECT_RETRIES)

    try:
        # Step 2: Pair
        print("\n[2] Pairing (3-step AES handshake)...")
        session_key, _client_random = await pair(client, mesh_name, mesh_password)
        print(f"  Session key: {len(session_key)} bytes [REDACTED]")
        print("  Pairing: SUCCESS")

        # Get MAC bytes for nonce
        mac_parts = mac.split(":")
        mac_bytes = bytes(int(p, 16) for p in mac_parts)

        # Step 3: Read + write char 1911
        print("\n[3] Char 1911 (status)...")
        try:
            raw_1911 = await client.read_gatt_char(TELINK_CHAR_STATUS)
            print(f"  Read ({len(raw_1911)} bytes): {_hex(bytes(raw_1911))}")
        except Exception as exc:
            print(f"  Read failed: {type(exc).__name__}: {exc}")

        print("  Writing 0x01 to enable notifications...")
        try:
            await client.write_gatt_char(TELINK_CHAR_STATUS, b"\x01", response=True)
            print("  Write 0x01: OK")
        except Exception as exc:
            print(f"  Write 0x01 FAILED: {type(exc).__name__}: {exc}")

        # Step 4: Power ON with dest_id=0 (unprovisioned default)
        print("\n[4] Power ON, dest_id=0 (unprovisioned default)...")
        pkt = _build_command(
            session_key, mac_bytes, 1, dest_id=0, opcode=TELINK_CMD_POWER, params=b"\x01"
        )
        await _send_and_observe(client, "power ON dest=0", pkt)

        # Step 5: Power ON with dest_id=0xFFFF (broadcast)
        print("\n[5] Power ON, dest_id=0xFFFF (broadcast)...")
        pkt = _build_command(
            session_key, mac_bytes, 2, dest_id=0xFFFF, opcode=TELINK_CMD_POWER, params=b"\x01"
        )
        await _send_and_observe(client, "power ON dest=0xFFFF", pkt)

        # Step 6: Power OFF with dest_id=0
        print("\n[6] Power OFF, dest_id=0...")
        pkt = _build_command(
            session_key, mac_bytes, 3, dest_id=0, opcode=TELINK_CMD_POWER, params=b"\x00"
        )
        await _send_and_observe(client, "power OFF dest=0", pkt)

        # Step 7: Power ON with random sequence
        print("\n[7] Power ON, random seq, dest_id=0...")
        rand_seq = int.from_bytes(os.urandom(3), "little")
        pkt = _build_command(
            session_key, mac_bytes, rand_seq, dest_id=0, opcode=TELINK_CMD_POWER, params=b"\x01"
        )
        await _send_and_observe(client, f"power ON rand_seq={rand_seq}", pkt)

        # Step 8: Power OFF with dest_id=0xFFFF
        print("\n[8] Power OFF, dest_id=0xFFFF (broadcast)...")
        pkt = _build_command(
            session_key, mac_bytes, 4, dest_id=0xFFFF, opcode=TELINK_CMD_POWER, params=b"\x00"
        )
        await _send_and_observe(client, "power OFF dest=0xFFFF", pkt)

        # Step 9: start_notify — LAST because it kills the connection
        print("\n[9] Testing start_notify (WARNING: may kill connection)...")
        try:
            await client.start_notify(TELINK_CHAR_STATUS, lambda _s, _d: None)
            print("  start_notify: OK (unexpected!)")
        except Exception as exc:
            print(f"  start_notify FAILED: {type(exc).__name__}: {exc}")
            print("  (Expected — Telink devices crash BlueZ D-Bus on start_notify)")

        # Summary
        print(f"\n{'=' * 60}")
        print("  DIAGNOSTIC SUMMARY")
        print(f"{'=' * 60}")
        print("  Pairing: OK")
        print(
            "  Commands sent: 5 (dest=0 ON, dest=0xFFFF ON, dest=0 OFF, rand ON, dest=0xFFFF OFF)"
        )
        print("  Observe lamp: did any command produce visible change?")
        print("  If NO change: command nonce format may be wrong")
        print("  If dest=0 works but not 0xFFFF: dest_id fix confirmed")

    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="BLE command protocol diagnostic")
    parser.add_argument("--mac", default=TARGET_DEVICE_MAC, help="Target MAC")
    parser.add_argument("--timeout", type=float, default=30.0, help="Connect timeout")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    try:
        asyncio.run(run_diagnostics(args.mac, args.timeout))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)
    except Exception as exc:
        print(f"\nFATAL: {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
