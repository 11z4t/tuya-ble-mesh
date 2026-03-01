#!/usr/bin/env python3
"""Headless power cycling via Shelly smart plug.

Turns off power to the Malmbergs BT device, waits, then turns it back on.
Optionally verifies the device appears via BLE scan afterwards.
"""

import argparse
import asyncio
import sys

from bleak import BleakScanner

# Add lib/ to path for imports
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "lib"))

from tuya_ble_mesh.power import (
    PowerControlError,
    ShellyPowerController,
)

TUYA_MESH_NAMES = ["out_of_mesh", "tymesh"]
DEFAULT_HOST = "192.168.1.50"


async def verify_ble_device(scan_duration: int = 15) -> bool:
    """Scan for Tuya/Malmbergs BLE devices after power cycle."""
    print(f"  Scanning BLE for {scan_duration}s...")
    devices = await BleakScanner.discover(timeout=scan_duration)
    for device in devices:
        name = device.name or ""
        for pattern in TUYA_MESH_NAMES:
            if name.lower().startswith(pattern.lower()):
                print(f"  Found: {device.name} ({device.address})")
                return True
    return False


async def run(host: str, off_time: float, verify: bool) -> bool:
    """Execute power cycle."""
    controller = ShellyPowerController(host)

    try:
        print(f"  Checking Shelly at {host}...")
        if not await controller.is_reachable():
            print(f"  FAIL: Shelly at {host} is unreachable")
            return False

        print(f"  Power cycling (off for {off_time}s)...")
        success = await controller.power_cycle(off_seconds=off_time)

        if not success:
            print("  FAIL: Power cycle command failed")
            return False

        print("  Power cycle complete.")

        if verify:
            print("  Waiting 10s for device to boot...")
            await asyncio.sleep(10.0)
            if await verify_ble_device():
                print("  OK: Device found via BLE")
                return True
            else:
                print("  FAIL: Device not found via BLE after power cycle")
                return False

        return True

    except PowerControlError as exc:
        print(f"  FAIL: {exc}")
        return False
    finally:
        await controller.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Power cycle BLE device via Shelly smart plug",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Shelly IP address (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--off-time",
        type=float,
        default=5.0,
        help="Seconds to keep power off (default: 5.0)",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify device appears via BLE scan after power cycle",
    )
    args = parser.parse_args()

    print("\n  Malmbergs BT Lab — Power Cycle")
    result = asyncio.run(run(args.host, args.off_time, args.verify))
    print(f"\n  Result: {'OK' if result else 'FAIL'}")
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
