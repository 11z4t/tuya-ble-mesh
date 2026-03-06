#!/usr/bin/env python3
"""Quick BLE scanner for Tuya BLE Mesh devices."""

import asyncio
import pathlib
import sys
from datetime import datetime

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# Add lib/ to path for imports
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))

from tuya_ble_mesh.const import TUYA_MESH_NAME_PATTERNS, TUYA_MESH_SERVICE_UUID


def detect_serial_sniffers() -> list[str]:
    """Detect serial BLE sniffers (Adafruit nRF51822 via CP210x)."""
    ports: list[str] = []
    for pattern in ("ttyUSB*", "ttyACM*"):
        ports.extend(str(p) for p in pathlib.Path("/dev").glob(pattern))
    return sorted(ports)


async def print_hardware_info() -> None:
    """Print lab hardware status before scanning."""
    print("  Hardware:")

    # HCI adapters
    proc = await asyncio.create_subprocess_exec(
        "hciconfig",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode == 0 and stdout:
        for line in stdout.decode().splitlines():
            if line and not line.startswith("\t"):
                adapter = line.split(":")[0]
                print(f"    BT adapter: {adapter} (active, HCI)")
    else:
        print("    BT adapter: none detected")

    # Serial sniffers
    sniffers = detect_serial_sniffers()
    if sniffers:
        for port in sniffers:
            print(f"    BLE sniffer: {port} (passive, serial/nRF)")
    else:
        print("    BLE sniffer: none detected")

    print()


def is_tuya_device(device: BLEDevice, adv: AdvertisementData) -> bool:
    """Check if device is likely a Tuya BLE Mesh device."""
    name = device.name or ""

    # Check name patterns
    for pattern in TUYA_MESH_NAME_PATTERNS:
        if name.lower().startswith(pattern.lower()):
            return True

    # Check service UUIDs
    return TUYA_MESH_SERVICE_UUID in (adv.service_uuids or [])


async def scan(duration: int = 15) -> None:
    """Scan for BLE devices and highlight Tuya/Malmbergs devices."""
    print(f"\n{'=' * 60}")
    print("  Tuya BLE Mesh Lab — BLE Scanner")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Duration: {duration}s")
    print(f"{'=' * 60}\n")

    await print_hardware_info()

    tuya_devices = []

    def callback(device: BLEDevice, adv: AdvertisementData):
        if is_tuya_device(device, adv):
            tuya_devices.append((device, adv))

    print("Scanning...")
    async with BleakScanner(detection_callback=callback):
        await asyncio.sleep(duration)

    # Deduplicate by MAC
    seen_macs = set()
    unique_tuya = []
    for device, adv in tuya_devices:
        if device.address not in seen_macs:
            seen_macs.add(device.address)
            unique_tuya.append((device, adv))

    if unique_tuya:
        print(f"\n🎯 Found {len(unique_tuya)} Tuya/Malmbergs device(s):\n")
        for device, adv in unique_tuya:
            print(f"  Name:    {device.name or 'N/A'}")
            print(f"  MAC:     {device.address}")
            print(f"  RSSI:    {adv.rssi} dBm")
            if adv.service_uuids:
                print(f"  UUIDs:   {', '.join(adv.service_uuids)}")
            if adv.manufacturer_data:
                for mid, data in adv.manufacturer_data.items():
                    print(f"  Mfr ID:  0x{mid:04X}")
                    print(f"  Mfr Data: {data.hex()}")
            if adv.service_data:
                for uuid, data in adv.service_data.items():
                    print(f"  Svc Data [{uuid}]: {data.hex()}")
            print()
    else:
        print("\n⚠️  No Tuya/Malmbergs devices found.")
        print("    Make sure your device is powered on.")
        print("    If paired, try factory resetting it (look for 'out_of_mesh').\n")


if __name__ == "__main__":
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    asyncio.run(scan(duration))
