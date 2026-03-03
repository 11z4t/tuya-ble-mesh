#!/usr/bin/env python3
"""BLE Command Protocol Diagnostic v15b — CCCD after pairing.

Key insight: start_notify crashes with EOFError if called before pairing.
Try: complete pairing first, THEN enable CCCD, THEN send commands.

Also tries manual CCCD descriptor write as fallback.

SECURITY: Session key hex is NEVER shown.
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
    TELINK_CMD_POWER,
    TELINK_CMD_WHITE_BRIGHTNESS,
)
from tuya_ble_mesh.crypto import (
    crypt_payload,
    generate_session_random,
    make_checksum,
    make_pair_packet,
    make_session_key,
)

_RETRIES = 5
_PAYLOAD_SIZE = 15
VENDOR_ID = bytes([0x60, 0x01])
MESH_NAME = b"out_of_mesh"
MESH_PASS = b"123456"


def _hex(data: bytes) -> str:
    return data.hex(" ")


def _make_cmd(key: bytes, mac: str, dest: int, cmd: int, data: bytes) -> bytes:
    """Build encrypted command packet (AWox-exact format)."""
    s = os.urandom(3)
    a = bytearray.fromhex(mac.replace(":", ""))
    a.reverse()
    nonce = bytes(a[0:4] + b"\x01" + s)
    d = struct.pack("<H", dest)
    payload = (d + struct.pack("B", cmd) + VENDOR_ID + data).ljust(_PAYLOAD_SIZE, b"\x00")
    check = make_checksum(key, nonce, payload)
    enc = crypt_payload(key, nonce, payload)
    return s + check[0:2] + enc


async def _connect(address: str, timeout: float) -> BleakClient:
    for attempt in range(1, _RETRIES + 1):
        try:
            dev = await BleakScanner.find_device_by_address(address, timeout=timeout)
            if dev is None:
                print(f"  Scan: not found ({attempt})")
                continue
            client = BleakClient(dev, timeout=timeout)
            await client.connect()
            print(f"  Connected (attempt {attempt})")
            return client
        except Exception as exc:
            backoff = min(2.0 * attempt, 8.0)
            print(f"  Attempt {attempt}: {type(exc).__name__}, {backoff:.0f}s...")
            with contextlib.suppress(Exception):
                p = await asyncio.create_subprocess_exec(
                    "bluetoothctl", "remove", address,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(p.wait(), timeout=5.0)
            await asyncio.sleep(backoff)
    raise ConnectionError("Failed to connect")


async def run(mac: str, timeout: float) -> None:
    print(f"\n{'=' * 60}")
    print(f"  BLE Diagnostic v15b — CCCD after pairing")
    print(f"  Target: {mac}")
    print(f"{'=' * 60}\n")

    # === Step 1: Connect ===
    print("[1] Connecting...")
    client = await _connect(mac, timeout)
    session_key = None

    try:
        # === Step 2: Full pairing handshake ===
        print("\n[2] Pairing (factory creds)...")
        client_random = generate_session_random()
        pair_pkt = make_pair_packet(MESH_NAME, MESH_PASS, client_random)

        await client.write_gatt_char(TELINK_CHAR_PAIRING, pair_pkt, response=True)
        print("  Pair request written")

        await client.write_gatt_char(TELINK_CHAR_STATUS, b"\x01", response=True)
        print("  Status enable (0x01) written to 1911")

        resp = bytes(await client.read_gatt_char(TELINK_CHAR_PAIRING))
        print(f"  Response: 0x{resp[0]:02X}")

        if resp[0] == 0x0E:
            print("  AUTH FAILED!")
            return
        if resp[0] != 0x0D:
            print(f"  UNEXPECTED: {_hex(resp)}")
            return

        device_random = resp[1:9]
        session_key = make_session_key(MESH_NAME, MESH_PASS, client_random, device_random)
        print(f"  Paired OK (key len={len(session_key)})")

        # Small delay after pairing
        await asyncio.sleep(1)

        # === Step 3: Try start_notify AFTER pairing ===
        print("\n[3] start_notify on 1911 (AFTER pairing)...")
        notifications = []

        def _on_notify(sender, data: bytearray):
            raw = bytes(data)
            notifications.append(raw)
            print(f"  >> NOTIFICATION ({len(raw)}B): {_hex(raw)}")
            if len(raw) >= 7 and session_key:
                a = bytearray.fromhex(mac.replace(":", ""))
                a.reverse()
                nonce = bytes(a[0:3] + raw[0:5])
                dec = crypt_payload(session_key, nonce, raw[7:])
                chk = make_checksum(session_key, nonce, dec)
                if chk[0:2] == raw[5:7]:
                    print(f"  >> DECRYPTED: {_hex(bytes(dec))}")

        notify_ok = False
        try:
            await client.start_notify(TELINK_CHAR_STATUS, _on_notify)
            notify_ok = True
            print("  start_notify OK!")
        except Exception as e:
            print(f"  start_notify FAILED: {type(e).__name__}: {e}")

            # Fallback: try manual CCCD write
            print("  Trying manual CCCD write (handle 0x0013)...")
            try:
                # CCCD UUID is 00002902-0000-1000-8000-00805f9b34fb
                # Enable notifications = 0x0100 (little-endian)
                for svc in client.services:
                    for char in svc.characteristics:
                        if char.uuid == TELINK_CHAR_STATUS:
                            for desc in char.descriptors:
                                if "2902" in desc.uuid:
                                    print(f"  Found CCCD: {desc.uuid} handle=0x{desc.handle:04X}")
                                    await client.write_gatt_descriptor(desc.handle, b"\x01\x00")
                                    print("  Manual CCCD write OK!")
                                    notify_ok = True
            except Exception as e2:
                print(f"  Manual CCCD also failed: {type(e2).__name__}: {e2}")

        # Wait for any spontaneous notifications
        print("\n  Waiting 3s for notifications...")
        await asyncio.sleep(3)
        if notifications:
            print(f"  Got {len(notifications)} notification(s)!")
        else:
            print("  No notifications yet")

        # === Step 4: Send commands ===
        print("\n[4] Sending commands...")

        tests = [
            ("Power OFF, dest=0, WR", 0, TELINK_CMD_POWER, b"\x00", True),
            ("Power ON, dest=0, WR", 0, TELINK_CMD_POWER, b"\x01", True),
            ("Power OFF, dest=0xFFFF, WNR", 0xFFFF, TELINK_CMD_POWER, b"\x00", False),
            ("Power ON, dest=0x01DC, WR", 0x01DC, TELINK_CMD_POWER, b"\x01", True),
            ("Brightness 50%, dest=0", 0, TELINK_CMD_WHITE_BRIGHTNESS, b"\x40", True),
        ]

        for name, dest, cmd, data, resp_flag in tests:
            print(f"\n  --- {name} ---")
            pkt = _make_cmd(session_key, mac, dest, cmd, data)
            print(f"  Pkt ({len(pkt)}B): {_hex(pkt)}")
            try:
                await client.write_gatt_char(TELINK_CHAR_COMMAND, pkt, response=resp_flag)
                mode = "WR" if resp_flag else "WNR"
                print(f"  Sent ({mode}). 4s...")
            except Exception as e:
                print(f"  SEND FAILED: {type(e).__name__}: {e}")
            await asyncio.sleep(4)

        print(f"\n  Total notifications received: {len(notifications)}")
        print(f"\n  REAGERADE LAMPAN PA NAGOT?")

    finally:
        with contextlib.suppress(Exception):
            if notify_ok:
                await client.stop_notify(TELINK_CHAR_STATUS)
        with contextlib.suppress(Exception):
            await client.disconnect()
        print("  Disconnected.")


def main() -> None:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--mac", default=TARGET_DEVICE_MAC)
    p.add_argument("--timeout", type=float, default=20.0)
    args = p.parse_args()
    try:
        asyncio.run(run(args.mac, args.timeout))
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except Exception as exc:
        print(f"\nFATAL: {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
