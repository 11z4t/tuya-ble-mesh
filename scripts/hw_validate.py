#!/usr/bin/env python3
"""Standalone hardware validation for Tuya BLE Mesh devices.

Runs a full validation sequence: scan → connect → provision → commands → disconnect.
Prints "VERIFY:" lines for visual confirmation by the operator.

Usage:
    python scripts/hw_validate.py --mac DC:23:4D:21:43:A5
    python scripts/hw_validate.py  # uses default MAC
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from datetime import datetime

# Add lib/ to path
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))

from tuya_ble_mesh.device import MeshDevice
from tuya_ble_mesh.scanner import scan_for_tuya_devices

DEFAULT_MAC = "DC:23:4D:21:43:A5"
DEFAULT_MESH_NAME = b"out_of_mesh"
DEFAULT_MESH_PASSWORD = b"123456"  # pragma: allowlist secret


def banner(title: str) -> None:
    """Print a section banner."""
    print(f"\n{'─' * 50}")
    print(f"  {title}")
    print(f"{'─' * 50}")


async def step_scan(target_mac: str) -> bool:
    """Step 1: Scan for devices."""
    banner("Step 1: BLE Scan")
    devices = await scan_for_tuya_devices(timeout=15.0)

    if not devices:
        print("  FAIL: No Tuya devices found")
        return False

    print(f"  Found {len(devices)} Tuya device(s)")
    found = False
    for dev in devices:
        marker = " ← TARGET" if dev.address.upper() == target_mac.upper() else ""
        print(f"    {dev.name} ({dev.address}) RSSI={dev.rssi}{marker}")
        if dev.address.upper() == target_mac.upper():
            found = True

    if not found:
        print(f"  WARN: Target {target_mac} not found in scan")
    return found


async def step_connect(device: MeshDevice) -> bool:
    """Step 2: Connect and provision."""
    banner("Step 2: Connect & Provision")
    try:
        await device.connect(timeout=30.0)
        print(f"  Connected to {device.address}")
        print(f"  Provisioned: {device.is_connected}")
        return device.is_connected
    except Exception as exc:
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        return False


async def step_commands(device: MeshDevice) -> bool:
    """Step 3: Send commands."""
    banner("Step 3: Commands")
    try:
        # Power ON
        await device.send_power(True)
        await asyncio.sleep(1.0)
        print("  VERIFY: Light should be ON")

        # Brightness
        await device.send_brightness(30)
        await asyncio.sleep(1.5)
        print("  VERIFY: Light should be DIM (~30/127)")

        await device.send_brightness(127)
        await asyncio.sleep(1.5)
        print("  VERIFY: Light should be FULL brightness")

        # Color temp
        await device.send_color_temp(0)
        await asyncio.sleep(1.5)
        print("  VERIFY: Light should be WARM")

        await device.send_color_temp(127)
        await asyncio.sleep(1.5)
        print("  VERIFY: Light should be COOL")

        # Power OFF
        await device.send_power(False)
        await asyncio.sleep(1.0)
        print("  VERIFY: Light should be OFF")

        return True
    except Exception as exc:
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        return False


async def step_disconnect(device: MeshDevice) -> bool:
    """Step 4: Disconnect."""
    banner("Step 4: Disconnect")
    try:
        await device.disconnect()
        print(f"  Disconnected from {device.address}")
        print(f"  Connected: {device.is_connected}")
        return not device.is_connected
    except Exception as exc:
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        return False


async def validate(mac: str) -> None:
    """Run full hardware validation."""
    print(f"\n{'═' * 50}")
    print("  Tuya BLE Mesh — Hardware Validation")
    print(f"  Target: {mac}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'═' * 50}")

    results: dict[str, bool] = {}

    # Step 1: Scan
    results["scan"] = await step_scan(mac)

    # Step 2: Connect
    device = MeshDevice(mac, DEFAULT_MESH_NAME, DEFAULT_MESH_PASSWORD)
    results["connect"] = await step_connect(device)

    if results["connect"]:
        # Step 3: Commands
        results["commands"] = await step_commands(device)

        # Step 4: Disconnect
        results["disconnect"] = await step_disconnect(device)
    else:
        results["commands"] = False
        results["disconnect"] = False
        print("\n  Skipping commands and disconnect (not connected)")

    # Summary
    banner("Summary")
    for step, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {step:12s}: {status}")

    total = sum(results.values())
    print(f"\n  {total}/{len(results)} steps passed")

    if total == len(results):
        print("\n  All hardware validation steps PASSED")
    else:
        print("\n  Some steps FAILED — check output above")


def main() -> None:
    """Parse arguments and run validation."""
    parser = argparse.ArgumentParser(description="Hardware validation for Tuya BLE Mesh")
    parser.add_argument(
        "--mac",
        default=DEFAULT_MAC,
        help=f"Target device MAC address (default: {DEFAULT_MAC})",
    )
    args = parser.parse_args()
    asyncio.run(validate(args.mac))


if __name__ == "__main__":
    main()
