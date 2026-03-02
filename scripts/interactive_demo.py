#!/usr/bin/env python3
"""Interactive step-by-step light demo.

Connects to the device once, then exposes individual command functions
that can be called from the REPL or sequentially.

Usage::

    python scripts/interactive_demo.py --mac DC:23:4D:21:43:A5

SECURITY: No secrets are logged or printed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Add lib/ to path
_LIB_DIR = str(Path(__file__).resolve().parent.parent / "lib")
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from tuya_ble_mesh.device import MeshDevice  # noqa: E402


async def run_demo(mac: str) -> None:
    """Run the interactive demo sequence."""
    mesh_name = b"out_of_mesh"
    mesh_password = b"123456"

    print("\n=== Interactive Light Demo ===")
    print(f"Target: {mac}\n")

    device = MeshDevice(mac, mesh_name, mesh_password)

    # Step 0: Connect
    print("--- STEG 0: Ansluter till enheten ---")
    try:
        await device.connect(timeout=30.0)
        print("RESULT: Connected and provisioned OK")
    except Exception as e:
        print(f"RESULT: FAIL — {e}")
        return

    try:
        # Step 1: Power ON
        print("\n--- STEG 1: POWER ON ---")
        print("VERIFY: Lampan ska TÄNDAS")
        await device.send_power(True)
        await asyncio.sleep(2)
        input("Tryck ENTER för nästa steg...")

        # Step 2: Power OFF
        print("\n--- STEG 2: POWER OFF ---")
        print("VERIFY: Lampan ska SLÄCKAS")
        await device.send_power(False)
        await asyncio.sleep(2)
        input("Tryck ENTER för nästa steg...")

        # Step 3: Blink 3x
        print("\n--- STEG 3: BLINK 3 gånger ---")
        print("VERIFY: Lampan ska blinka 3 gånger (on-off-on-off-on-off)")
        for _i in range(3):
            await device.send_power(True)
            await asyncio.sleep(0.8)
            await device.send_power(False)
            await asyncio.sleep(0.8)
        await asyncio.sleep(1)
        input("Tryck ENTER för nästa steg...")

        # Step 4: Dim up 0 → 100%
        print("\n--- STEG 4: DIM UP (0% → 100%) ---")
        print("VERIFY: Lampan ska tändas och sakta öka i ljusstyrka")
        await device.send_power(True)
        await asyncio.sleep(0.5)
        for level in range(1, 128, 5):
            await device.send_brightness(level)
            await asyncio.sleep(0.15)
        await device.send_brightness(127)
        await asyncio.sleep(1)
        input("Tryck ENTER för nästa steg...")

        # Step 5: Dim down 100% → 0
        print("\n--- STEG 5: DIM DOWN (100% → 0%) ---")
        print("VERIFY: Lampan ska sakta minska i ljusstyrka")
        for level in range(127, 0, -5):
            await device.send_brightness(level)
            await asyncio.sleep(0.15)
        await device.send_brightness(1)
        await asyncio.sleep(1)
        input("Tryck ENTER för nästa steg...")

        # Step 6: Power OFF (final)
        print("\n--- STEG 6: POWER OFF (slutlig) ---")
        print("VERIFY: Lampan ska SLÄCKAS")
        await device.send_power(False)
        await asyncio.sleep(1)

        print("\n=== Demo klar! ===")

    except Exception as e:
        print(f"\nERROR: {e}")
    finally:
        await device.disconnect()
        print("Disconnected.")


def main() -> None:
    """Parse arguments and run the demo."""
    parser = argparse.ArgumentParser(description="Interactive light demo")
    parser.add_argument(
        "--mac",
        default="DC:23:4D:21:43:A5",
        help="BLE MAC address (default: DC:23:4D:21:43:A5)",
    )
    args = parser.parse_args()
    asyncio.run(run_demo(args.mac))


if __name__ == "__main__":
    main()
