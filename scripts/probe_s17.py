#!/usr/bin/env python3
"""S17 Smart Plug GATT probe — diagnostic for c44f42b1 characteristics.

Probes the S17 plug's non-standard GATT characteristics to understand
the pairing and command protocol. The S17 uses c44f42b1 UUIDs instead of
the standard Telink 1910-1914 UUIDs.

SECURITY: Raw response bytes are NEVER logged (may contain key material).
Only lengths, first byte (opcode), properties, and handle numbers are shown.
"""

import argparse
import asyncio
import contextlib
import logging
import pathlib
import sys
from datetime import datetime

from bleak import BleakClient, BleakScanner

# Add lib/ to path for imports
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))

from tuya_ble_mesh.crypto import (
    generate_session_random,
    make_pair_packet,
)

_LOGGER = logging.getLogger(__name__)

# --- S17-specific constants (local to script, not in const.py) ---

S17_MAC = "E7:A7:C0:11:89:D1"
S17_SERVICE = "0000fe07-0000-1000-8000-00805f9b34fb"
S17_CHAR_NOTIFY = "c44f42b1-f5cf-479b-b515-9f1bb0099c99"  # read+notify
S17_CHAR_WRITE = "c44f42b1-f5cf-479b-b515-9f1bb0099c98"  # write-no-resp

# Factory default credentials (same as LED driver)
MESH_NAME = b"out_of_mesh"
MESH_PASS = b"123456"

_RETRIES = 5


async def _ble_remove(address: str) -> None:
    """Ask BlueZ to remove cached device."""
    with contextlib.suppress(Exception):
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl",
            "remove",
            address,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=5.0)


async def _connect(address: str, timeout: float) -> BleakClient:
    """Connect with retry and BlueZ cache clearing."""
    for attempt in range(1, _RETRIES + 1):
        try:
            dev = await BleakScanner.find_device_by_address(
                address,
                timeout=timeout,
            )
            if dev is None:
                print(f"  Scan: not found (attempt {attempt})")
                continue
            client = BleakClient(dev, timeout=timeout)
            await client.connect()
            print(f"  Connected (attempt {attempt})")
            return client
        except Exception as exc:
            backoff = min(2.0 * attempt, 8.0)
            print(f"  Attempt {attempt}: {type(exc).__name__}, {backoff:.0f}s...")
            await _ble_remove(address)
            await asyncio.sleep(backoff)
    msg = f"Failed to connect to {address} after {_RETRIES} attempts"
    raise ConnectionError(msg)


async def step_enumerate(client: BleakClient) -> list[dict[str, object]]:
    """[2] Enumerate ALL GATT services and characteristics."""
    print("\n[2] Enumerating GATT services...")
    results: list[dict[str, object]] = []

    for service in client.services:
        chars_info: list[dict[str, object]] = []
        for char in service.characteristics:
            descs = [{"uuid": d.uuid, "handle": f"0x{d.handle:04X}"} for d in char.descriptors]
            chars_info.append(
                {
                    "uuid": char.uuid,
                    "handle": f"0x{char.handle:04X}",
                    "properties": list(char.properties),
                    "descriptors": descs,
                }
            )
            props = ", ".join(char.properties)
            print(f"    Char: {char.uuid}  handle=0x{char.handle:04X}  [{props}]")
            for d in char.descriptors:
                print(f"      Desc: {d.uuid}  handle=0x{d.handle:04X}")

        svc_info: dict[str, object] = {
            "uuid": service.uuid,
            "description": service.description or "",
            "characteristics": chars_info,
        }
        results.append(svc_info)
        desc_str = f" ({service.description})" if service.description else ""
        print(f"  Service: {service.uuid}{desc_str}  [{len(chars_info)} chars]")

    # Check for Telink service presence
    svc_uuids = [str(s["uuid"]) for s in results]
    has_telink = any("1910" in u for u in svc_uuids)
    has_c44f = any("c44f42b1" in u for u in svc_uuids)
    print(f"\n  Telink 1910 present: {has_telink}")
    print(f"  c44f42b1 present: {has_c44f}")

    return results


async def step_read_baseline(client: BleakClient) -> int | None:
    """[3] Read 9c99 characteristic (baseline value)."""
    print("\n[3] Reading 9c99 (baseline)...")
    try:
        data = bytes(await client.read_gatt_char(S17_CHAR_NOTIFY))
        print(f"  Read OK: {len(data)} bytes, first byte: 0x{data[0]:02X}")
        return data[0]
    except Exception as exc:
        print(f"  Read FAILED: {type(exc).__name__}")
        return None


async def step_pair_attempt(client: BleakClient) -> int | None:
    """[4] Write pair packet to 9c98, then read 9c99 for response."""
    print("\n[4] Pair attempt (write 9c98, read 9c99)...")
    client_random = generate_session_random()
    pair_pkt = make_pair_packet(MESH_NAME, MESH_PASS, client_random)
    print(f"  Pair packet: {len(pair_pkt)} bytes (opcode: 0x{pair_pkt[0]:02X})")

    try:
        await client.write_gatt_char(S17_CHAR_WRITE, pair_pkt, response=False)
        print("  Write to 9c98 OK (write-without-response)")
    except Exception as exc:
        print(f"  Write to 9c98 FAILED: {type(exc).__name__}")
        return None

    await asyncio.sleep(1.0)

    try:
        data = bytes(await client.read_gatt_char(S17_CHAR_NOTIFY))
        print(f"  Read 9c99 after pair: {len(data)} bytes, first byte: 0x{data[0]:02X}")
        return data[0]
    except Exception as exc:
        print(f"  Read 9c99 after pair FAILED: {type(exc).__name__}")
        return None


async def step_notifications(
    client: BleakClient,
) -> list[dict[str, object]]:
    """[5] Try notification subscription on 9c99."""
    print("\n[5] Notification subscription on 9c99...")
    events: list[dict[str, object]] = []
    notify_ok = False

    def _on_notify(_sender: object, data: bytearray) -> None:
        ts = datetime.now().isoformat(timespec="milliseconds")
        events.append(
            {
                "timestamp": ts,
                "length": len(data),
                "first_byte": f"0x{data[0]:02X}" if data else "empty",
            }
        )
        print(f"  >> NOTIFICATION: {len(data)} bytes, first=0x{data[0]:02X} @ {ts}")

    # Try start_notify
    try:
        await client.start_notify(S17_CHAR_NOTIFY, _on_notify)
        notify_ok = True
        print("  start_notify OK!")
    except Exception as exc:
        print(f"  start_notify FAILED: {type(exc).__name__}: {exc}")

        # Fallback: manual CCCD write
        print("  Trying manual CCCD write...")
        try:
            for svc in client.services:
                for char in svc.characteristics:
                    if char.uuid == S17_CHAR_NOTIFY:
                        for desc in char.descriptors:
                            if "2902" in desc.uuid:
                                print(f"    Found CCCD: handle=0x{desc.handle:04X}")
                                await client.write_gatt_descriptor(
                                    desc.handle,
                                    b"\x01\x00",
                                )
                                notify_ok = True
                                print("    Manual CCCD write OK!")
        except Exception as exc2:
            print(f"    Manual CCCD also failed: {type(exc2).__name__}")

    return events, notify_ok


async def step_pair_with_notify(
    client: BleakClient,
    notify_ok: bool,
    events: list[dict[str, object]],
) -> None:
    """[6] Write pair packet while notifications are active."""
    if not notify_ok:
        print("\n[6] Skipping (notifications not active)")
        return

    print("\n[6] Pair attempt with notifications active...")
    client_random = generate_session_random()
    pair_pkt = make_pair_packet(MESH_NAME, MESH_PASS, client_random)

    count_before = len(events)
    try:
        await client.write_gatt_char(S17_CHAR_WRITE, pair_pkt, response=False)
        print("  Write to 9c98 OK, waiting 3s for notification response...")
    except Exception as exc:
        print(f"  Write FAILED: {type(exc).__name__}")
        return

    await asyncio.sleep(3.0)
    new_events = len(events) - count_before
    print(f"  New notifications after pair: {new_events}")


async def step_raw_command(
    client: BleakClient,
    notify_ok: bool,
    events: list[dict[str, object]],
) -> None:
    """[7] Try raw command write to 9c98 (power on, no encryption)."""
    print("\n[7] Raw command test (unencrypted power on)...")

    # Try a simple Tuya DP payload: dp_id=121(power), type=2(value), len=1, val=1(on)
    # This is unencrypted — may not work but worth trying
    raw_dp = bytes([0x79, 0x02, 0x01, 0x01])
    count_before = len(events)

    try:
        await client.write_gatt_char(S17_CHAR_WRITE, raw_dp, response=False)
        print(f"  Write raw DP OK ({len(raw_dp)} bytes, dp_id=0x79 power=ON)")
    except Exception as exc:
        print(f"  Write FAILED: {type(exc).__name__}")
        return

    await asyncio.sleep(2.0)
    new_events = len(events) - count_before
    print(f"  Notifications after raw cmd: {new_events}")

    # Also try 0x01 (simple on) as some devices use single-byte commands
    try:
        await client.write_gatt_char(S17_CHAR_WRITE, b"\x01", response=False)
        print("  Write 0x01 OK")
    except Exception as exc:
        print(f"  Write 0x01 FAILED: {type(exc).__name__}")

    await asyncio.sleep(2.0)
    new_events_total = len(events) - count_before
    print(f"  Total new notifications: {new_events_total}")


def print_report(
    *,
    mac: str,
    services: list[dict[str, object]],
    baseline: int | None,
    after_pair: int | None,
    events: list[dict[str, object]],
    notify_ok: bool,
    start_time: str,
) -> None:
    """[8] Print summary report."""
    print(f"\n{'=' * 60}")
    print("  S17 Plug Probe Report")
    print(f"  Device: {mac}")
    print(f"  Time: {start_time}")
    print(f"{'=' * 60}")

    print(f"\n  Services found: {len(services)}")
    for svc in services:
        chars = svc.get("characteristics", [])
        count = len(chars) if isinstance(chars, list) else 0
        print(f"    {svc['uuid']}: {count} chars")

    print(f"\n  9c99 baseline read: {f'0x{baseline:02X}' if baseline is not None else 'FAILED'}")
    pair_str = f"0x{after_pair:02X}" if after_pair is not None else "FAILED"
    print(f"  9c99 after pair write: {pair_str}")
    changed = baseline != after_pair if baseline is not None and after_pair is not None else None
    print(f"  Value changed after pair: {changed}")

    print(f"\n  Notifications enabled: {notify_ok}")
    print(f"  Total notifications: {len(events)}")
    for evt in events:
        print(f"    {evt['timestamp']}: {evt['length']}B first={evt['first_byte']}")

    print("\n  Observations:")
    if baseline == 0x42 and after_pair == 0x42:
        print("    - 9c99 returns 0x42 unchanged — pair protocol differs from Telink")
    if not events:
        print("    - No notifications received — may need different subscription method")
    if changed:
        print("    - Value CHANGED after pair write — pairing may be partially working")

    print(f"\n{'=' * 60}\n")


async def probe(mac: str, timeout: float) -> None:
    """Main probe flow."""
    start_time = datetime.now().isoformat(timespec="seconds")

    print(f"\n{'=' * 60}")
    print("  S17 Plug GATT Probe")
    print(f"  Target: {mac}")
    print(f"  Started: {start_time}")
    print(f"{'=' * 60}")

    # [1] Connect
    print("\n[1] Connecting...")
    client = await _connect(mac, timeout)

    try:
        # [2] Enumerate GATT
        services = await step_enumerate(client)

        # [3] Read baseline
        baseline = await step_read_baseline(client)

        # [4] Pair attempt
        after_pair = await step_pair_attempt(client)

        # [5] Notification subscription
        events, notify_ok = await step_notifications(client)

        # [6] Pair with notifications
        await step_pair_with_notify(client, notify_ok, events)

        # [7] Raw command test
        await step_raw_command(client, notify_ok, events)

        # [8] Report
        print_report(
            mac=mac,
            services=services,
            baseline=baseline,
            after_pair=after_pair,
            events=events,
            notify_ok=notify_ok,
            start_time=start_time,
        )

    finally:
        with contextlib.suppress(Exception):
            await client.disconnect()
        print("  Disconnected.")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Probe S17 smart plug GATT characteristics",
    )
    parser.add_argument(
        "--mac",
        default=S17_MAC,
        help=f"Target device MAC address (default: {S17_MAC})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="BLE scan/connect timeout in seconds (default: 20)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    try:
        asyncio.run(probe(args.mac, args.timeout))
    except KeyboardInterrupt:
        print("\nInterrupted.")
    except ConnectionError as exc:
        print(f"\nConnection failed: {exc}")
        print("Try power cycling the device: python scripts/power_cycle.py")
        sys.exit(1)
    except Exception as exc:
        print(f"\nFATAL: {type(exc).__name__}: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
