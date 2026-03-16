#!/usr/bin/env python3
"""Debug script for PLAT-696: Test full pairing + command flow with extensive logging.

This script:
1. Connects to device with factory defaults
2. Pairs and derives session key
3. Sets new mesh credentials (CRITICAL for leaving pairing mode)
4. Sends power ON command
5. Logs every byte sent/received

Run with: python3 scripts/test_pairing_debug.py
"""

import asyncio
import logging
import sys
from pathlib import Path

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from tuya_ble_mesh.device import MeshDevice

# Configure logging to show ALL debug output
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/tmp/plat696_debug.log", mode="w"),
    ],
)

_LOGGER = logging.getLogger(__name__)

# Device configuration
DEVICE_MAC = "DC:23:4D:21:43:A5"  # Malmbergs LED Driver

# PLAT-696 v2 Test: We now pair with the CONFIGURED credentials (not factory defaults).
# This means the device must ALREADY be configured with these credentials.
#
# Test scenario 1: Fresh device (factory reset) → use factory defaults
MESH_NAME = b"out_of_mesh"
MESH_PASSWORD = b"123456"
#
# Test scenario 2: Device previously paired (e.g. with Tuya app) → use those credentials
# MESH_NAME = b"your_mesh_name"
# MESH_PASSWORD = b"your_password"

async def main():
    """Test pairing and command flow."""
    _LOGGER.info("=" * 80)
    _LOGGER.info("PLAT-696 v2 Debug Test — Pairing + Command Flow")
    _LOGGER.info("=" * 80)
    _LOGGER.info("Device: %s", DEVICE_MAC)
    _LOGGER.info("Mesh credentials: %s / %s", MESH_NAME, MESH_PASSWORD)
    _LOGGER.info("=" * 80)
    _LOGGER.info("IMPORTANT: Device must ALREADY be configured with these credentials!")
    _LOGGER.info("  - If fresh from factory reset → use out_of_mesh / 123456")
    _LOGGER.info("  - If previously paired (Tuya app) → use those credentials")
    _LOGGER.info("=" * 80)

    # Create device with configured credentials
    device = MeshDevice(
        DEVICE_MAC,
        MESH_NAME,
        MESH_PASSWORD,
        adapter="hci0",  # Force local adapter
    )

    try:
        _LOGGER.info("Step 1: Connecting and pairing...")
        async with device:
            _LOGGER.info("✓ Connection and pairing successful")
            _LOGGER.info("Session key established: %d bytes [REDACTED]",
                        len(device._conn.session_key) if device._conn.session_key else 0)

            # Wait a bit after pairing
            _LOGGER.info("Waiting 2 seconds after pairing...")
            await asyncio.sleep(2)

            _LOGGER.info("Step 2: Sending power ON command...")
            await device.send_power(True)
            _LOGGER.info("✓ Power ON command sent")

            # Wait to see if device responds
            _LOGGER.info("Waiting 3 seconds for device response...")
            await asyncio.sleep(3)

            _LOGGER.info("Step 3: Sending power OFF command...")
            await device.send_power(False)
            _LOGGER.info("✓ Power OFF command sent")

            # Wait again
            _LOGGER.info("Waiting 3 seconds...")
            await asyncio.sleep(3)

            _LOGGER.info("Step 4: Sending brightness 50%...")
            await device.send_brightness(50)
            _LOGGER.info("✓ Brightness command sent")

            # Final wait
            _LOGGER.info("Waiting 3 seconds before disconnect...")
            await asyncio.sleep(3)

        _LOGGER.info("✓ Test completed successfully")
        _LOGGER.info("=" * 80)
        _LOGGER.info("RESULT INTERPRETATION:")
        _LOGGER.info("  [✓] Pairing succeeded → credentials were correct")
        _LOGGER.info("  [✓] Commands sent → session key and encryption working")
        _LOGGER.info("  [ ] Check if lamp RESPONDED to commands (on/off/brightness)")
        _LOGGER.info("")
        _LOGGER.info("VISUAL CHECK:")
        _LOGGER.info("  - Did lamp turn ON when power ON sent?")
        _LOGGER.info("  - Did lamp turn OFF when power OFF sent?")
        _LOGGER.info("  - Did lamp change brightness to 50%%?")
        _LOGGER.info("")
        _LOGGER.info("If YES to all → PLAT-696 is FIXED!")
        _LOGGER.info("If NO → check logs at /tmp/plat696_debug.log for errors")
        _LOGGER.info("=" * 80)

    except Exception as exc:
        _LOGGER.error("Test failed: %s", exc, exc_info=True)
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
