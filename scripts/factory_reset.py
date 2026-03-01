#!/usr/bin/env python3
"""Headless factory reset via rapid Shelly power cycling.

Malmbergs BT devices factory reset when power cycled 3-5 times quickly.
After reset, the device advertises as "out_of_mesh" (ready for provisioning).
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

DEFAULT_HOST = "192.168.1.50"


async def scan_for_out_of_mesh(scan_duration: int = 20) -> bool:
    """Scan BLE for a device advertising as 'out_of_mesh' (factory reset)."""
    print(f"  Scanning BLE for 'out_of_mesh' ({scan_duration}s)...")
    devices = await BleakScanner.discover(timeout=scan_duration)
    for device in devices:
        name = device.name or ""
        if name.lower().startswith("out_of_mesh"):
            print(f"  Found: {device.name} ({device.address})")
            return True
    return False


async def run(host: str, cycles: int, interval: float) -> bool:
    """Execute factory reset sequence."""
    controller = ShellyPowerController(host)

    try:
        print(f"  Checking Shelly at {host}...")
        if not await controller.is_reachable():
            print(f"  FAIL: Shelly at {host} is unreachable")
            return False

        print(f"  Factory reset: {cycles} cycles, {interval}s interval...")
        success = await controller.factory_reset_cycle(cycles=cycles, interval=interval)

        if not success:
            print("  FAIL: Factory reset cycle failed")
            return False

        print("  Rapid cycling complete. Waiting 10s for device to boot...")
        await asyncio.sleep(10.0)

        if await scan_for_out_of_mesh():
            print("  OK: Device is in factory reset mode (out_of_mesh)")
            return True
        else:
            print("  FAIL: Device not advertising as 'out_of_mesh'")
            print("  Tip: Try increasing --cycles or check device proximity")
            return False

    except PowerControlError as exc:
        print(f"  FAIL: {exc}")
        return False
    finally:
        await controller.close()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Factory reset Malmbergs BT device via rapid power cycling",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"Shelly IP address (default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=5,
        help="Number of rapid power cycles (default: 5)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between on/off cycles (default: 1.0)",
    )
    args = parser.parse_args()

    print("\n  Malmbergs BT Lab — Factory Reset")
    result = asyncio.run(run(args.host, args.cycles, args.interval))
    print(f"\n  Result: {'OK' if result else 'FAIL'}")
    sys.exit(0 if result else 1)


if __name__ == "__main__":
    main()
