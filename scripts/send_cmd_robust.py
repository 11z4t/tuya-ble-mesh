#!/usr/bin/env python3
"""Robust command sender — pair + command, skip status enable.

Key findings from analysis:
- Credentials: out_of_mesh / 123456 (CONFIRMED via device proof)
- App uses Write Command (no response) for commands
- retsimx vendor_id = 0x1102
- Opcode might need 0xC0 OR mask
- Skip status enable (causes connection drop)
"""

import asyncio
import contextlib
import os
import pathlib
import struct
import sys

from bleak import BleakClient, BleakScanner

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))

from tuya_ble_mesh.const import (
    TARGET_DEVICE_MAC,
    TELINK_CHAR_COMMAND,
    TELINK_CHAR_PAIRING,
    TELINK_CHAR_STATUS,
)
from tuya_ble_mesh.crypto import (
    crypt_payload,
    generate_session_random,
    make_checksum,
    make_pair_packet,
    make_session_key,
)

MESH_NAME = b"out_of_mesh"
MESH_PASS = b"123456"


def _hex(data: bytes) -> str:
    return data.hex(" ")


def _make_cmd(
    key: bytes,
    mac: str,
    dest: int,
    opcode: int,
    vendor_id: bytes,
    params: bytes,
) -> bytes:
    """Build encrypted command packet."""
    seq = os.urandom(3)
    a = bytearray.fromhex(mac.replace(":", ""))
    a.reverse()
    nonce = bytes(a[0:4] + b"\x01" + seq)
    payload = (
        struct.pack("<H", dest)
        + struct.pack("B", opcode)
        + vendor_id
        + params
    ).ljust(15, b"\x00")
    check = make_checksum(key, nonce, payload)
    enc = crypt_payload(key, nonce, payload)
    return seq + check[0:2] + enc


async def main() -> None:
    mac = TARGET_DEVICE_MAC
    print(f"\n{'=' * 60}")
    print(f"  Robust Command Test")
    print(f"  Target: {mac}")
    print(f"  Creds: out_of_mesh / 123456 (confirmed)")
    print(f"{'=' * 60}\n")

    # Connect
    print("[1] Connecting...")
    for attempt in range(1, 6):
        try:
            dev = await BleakScanner.find_device_by_address(mac, timeout=15)
            if dev is None:
                print(f"  Not found ({attempt})")
                continue
            client = BleakClient(dev, timeout=15)
            await client.connect()
            print(f"  Connected (attempt {attempt})")
            break
        except Exception as e:
            print(f"  {attempt}: {type(e).__name__}: {e}")
            with contextlib.suppress(Exception):
                p = await asyncio.create_subprocess_exec(
                    "bluetoothctl", "remove", mac,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(p.wait(), timeout=5)
            await asyncio.sleep(3)
    else:
        print("  FAILED to connect")
        return

    try:
        # Step 2: Pair
        print("\n[2] Pairing...")
        cr = generate_session_random()
        pp = make_pair_packet(MESH_NAME, MESH_PASS, cr)
        await client.write_gatt_char(TELINK_CHAR_PAIRING, pp, response=True)
        resp = bytes(await client.read_gatt_char(TELINK_CHAR_PAIRING))
        if resp[0] != 0x0D:
            print(f"  Pair failed: 0x{resp[0]:02X}")
            return
        dr = resp[1:9]
        key = make_session_key(MESH_NAME, MESH_PASS, cr, dr)
        print(f"  Paired OK (key len={len(key)})")

        # Step 3: Skip status enable (causes disconnection!)
        # Instead, go straight to commands
        print("\n[3] Sending commands (NO status enable)...")
        await asyncio.sleep(0.5)

        # Test matrix: different vendor IDs, destinations, opcodes
        tests = [
            # (label, dest, opcode, vendor_id, params, use_response)
            # retsimx vendor 0x1102 (written big-endian in command)
            ("Power ON, dest=0, vendor=0x1102, op=0xD0, WC",
             0, 0xD0, bytes([0x11, 0x02]), b"\x01", False),
            ("Power OFF, dest=0, vendor=0x1102, op=0xD0, WC",
             0, 0xD0, bytes([0x11, 0x02]), b"\x00", False),
            # Same with Write Request
            ("Power ON, dest=0, vendor=0x1102, op=0xD0, WR",
             0, 0xD0, bytes([0x11, 0x02]), b"\x01", True),
            # AWox vendor 0x6001 (our original)
            ("Power ON, dest=0, vendor=0x6001, op=0xD0, WC",
             0, 0xD0, bytes([0x60, 0x01]), b"\x01", False),
            ("Power OFF, dest=0, vendor=0x6001, op=0xD0, WC",
             0, 0xD0, bytes([0x60, 0x01]), b"\x00", False),
            # Broadcast destination
            ("Power ON, dest=0xFFFF, vendor=0x1102, WC",
             0xFFFF, 0xD0, bytes([0x11, 0x02]), b"\x01", False),
            # With opcode 0xC0 | 0x10 = 0xD0 (same, just explicit)
            ("Power ON, dest=0, vendor=0x1102, op=0x10|0xC0, WC",
             0, 0x10 | 0xC0, bytes([0x11, 0x02]), b"\x01", False),
            # dest=1 (device may have mesh_id=1 after factory reset)
            ("Power ON, dest=1, vendor=0x1102, WC",
             1, 0xD0, bytes([0x11, 0x02]), b"\x01", False),
        ]

        for i, (label, dest, opcode, vendor_id, params, use_resp) in enumerate(tests):
            print(f"\n  --- Test {i+1}: {label} ---")
            try:
                cmd = _make_cmd(key, mac, dest, opcode, vendor_id, params)
                print(f"  Packet: {_hex(cmd)}")
                await client.write_gatt_char(
                    TELINK_CHAR_COMMAND, cmd, response=use_resp
                )
                mode = "WR" if use_resp else "WC"
                print(f"  Sent ({mode})")
            except Exception as e:
                print(f"  FAILED: {type(e).__name__}: {e}")
                # Try to check if still connected
                if not client.is_connected:
                    print("  Connection lost! Trying to reconnect...")
                    try:
                        dev = await BleakScanner.find_device_by_address(mac, timeout=10)
                        if dev:
                            client = BleakClient(dev, timeout=15)
                            await client.connect()
                            # Re-pair
                            cr = generate_session_random()
                            pp = make_pair_packet(MESH_NAME, MESH_PASS, cr)
                            await client.write_gatt_char(
                                TELINK_CHAR_PAIRING, pp, response=True
                            )
                            resp = bytes(await client.read_gatt_char(TELINK_CHAR_PAIRING))
                            if resp[0] == 0x0D:
                                dr = resp[1:9]
                                key = make_session_key(MESH_NAME, MESH_PASS, cr, dr)
                                print("  Reconnected + re-paired")
                            else:
                                print("  Re-pair failed")
                                break
                    except Exception as e2:
                        print(f"  Reconnect failed: {e2}")
                        break
            await asyncio.sleep(3)
            print("  Reagerade lampan?")

        # Step 4: Now try WITH status enable (may disconnect, that's OK)
        print("\n\n[4] Now trying WITH status enable...")
        if client.is_connected:
            try:
                await client.write_gatt_char(TELINK_CHAR_STATUS, b"\x01", response=True)
                print("  Status enable OK!")
                await asyncio.sleep(1)

                cmd = _make_cmd(key, mac, 0, 0xD0, bytes([0x11, 0x02]), b"\x00")
                await client.write_gatt_char(TELINK_CHAR_COMMAND, cmd, response=False)
                print("  Power OFF sent after status enable")
                await asyncio.sleep(3)
            except Exception as e:
                print(f"  Status enable caused: {type(e).__name__}: {e}")

    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()
        print("\n  Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
