#!/usr/bin/env python3
"""Provisioning attempt for Tuya BLE Mesh devices.

Confirmed: The Malmbergs LED Driver 9952126 uses the Tuya Proprietary
Mesh protocol with Telink-based GATT UUIDs. This script attempts
local provisioning using the default mesh name + password.

Protocol flow (Tuya Proprietary Mesh):
1. Connect to device advertising as "out_of_mesh"
2. Subscribe to command notify characteristic (1911)
3. Write default credentials to pairing characteristic (1913)
4. Observe response on notify channel
5. If successful, assign new mesh name/password

SECURITY: Only direction, UUID, timestamp, and byte length are logged.
Raw payload content is NEVER logged (may contain key material).

Tip: Run sniff.py in a parallel tmux window for passive packet capture.
"""

import argparse
import asyncio
import contextlib
import logging
import pathlib
import sys
from datetime import datetime

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

# Add lib/ to path for imports
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "lib"))

from tuya_ble_mesh.const import (
    TARGET_DEVICE_MAC,
    TELINK_CHAR_OTA,
    TELINK_CHAR_PAIRING,
    TELINK_CHAR_STATUS,
    TUYA_MESH_DEFAULT_NAME,
    TUYA_MESH_DEFAULT_PASSWORD,
)
from tuya_ble_mesh.exceptions import (
    BLEDeviceNotFoundError,
    BLETimeoutError,
)

_LOGGER = logging.getLogger(__name__)


async def scan_for_device(mac: str, timeout: float = 15.0) -> BLEDevice:
    """Find a BLE device by MAC address.

    Args:
        mac: Target device MAC address.
        timeout: Scan timeout in seconds.

    Returns:
        The discovered BLEDevice.

    Raises:
        BLEDeviceNotFoundError: If device is not found within timeout.
    """
    _LOGGER.info("Scanning for %s (timeout: %.0fs)...", mac, timeout)

    device: BLEDevice | None = None

    def callback(dev: BLEDevice, adv: AdvertisementData) -> None:
        nonlocal device
        if dev.address.upper() == mac.upper():
            device = dev
            _LOGGER.info("Found: %s (%s, RSSI: %d)", dev.name, dev.address, adv.rssi)

    scanner = BleakScanner(detection_callback=callback)
    await scanner.start()

    elapsed = 0.0
    step = 0.5
    while elapsed < timeout and device is None:
        await asyncio.sleep(step)
        elapsed += step

    await scanner.stop()

    if device is None:
        raise BLEDeviceNotFoundError(f"Device {mac} not found after {timeout:.0f}s scan")

    return device


async def provision(args: argparse.Namespace) -> None:
    """Main provisioning flow."""
    scan_time = datetime.now().isoformat(timespec="seconds")

    print(f"\n{'=' * 60}")
    print("  Tuya BLE Mesh — Provisioning Attempt")
    print(f"  Target: {args.mac}")
    print("  Variant: tuya_proprietary (Telink)")
    print(f"  Started: {scan_time}")
    print(f"  Dry run: {args.dry_run}")
    print(f"{'=' * 60}\n")

    # Step 1: Find device
    device = await scan_for_device(args.mac, timeout=args.scan_timeout)
    device_name = device.name or "Unknown"
    print(f"Found: {device_name} ({device.address})")

    if args.dry_run:
        print("\n[DRY RUN] Would connect and attempt provisioning.")
        print(f"  Pairing char: {TELINK_CHAR_PAIRING}")
        print(f"  Notify char: {TELINK_CHAR_STATUS}")
        print(f"  Mesh name: {TUYA_MESH_DEFAULT_NAME}")
        print("  Password: [REDACTED, length: 6]")
        print("\nDry run complete. Use --no-dry-run to actually attempt provisioning.")
        return

    # Step 2: Connect
    print(f"\nConnecting to {device_name}...")

    notify_events: list[dict[str, object]] = []

    async with BleakClient(device, timeout=args.connect_timeout) as client:
        if not client.is_connected:
            raise BLETimeoutError(f"Failed to connect to {args.mac}")

        print(f"Connected. MTU: {client.mtu_size}")

        # Step 3: Read current state of characteristics (length only)
        print("\nReading characteristic state before provisioning...")
        for char_uuid, label in [
            (TELINK_CHAR_STATUS, "Status (1911)"),
            (TELINK_CHAR_PAIRING, "Pairing (1913)"),
            (TELINK_CHAR_OTA, "OTA/Status (1914)"),
        ]:
            try:
                data = await client.read_gatt_char(char_uuid)
                print(f"  {label}: {len(data)} bytes [content redacted]")
            except Exception as exc:
                print(f"  {label}: read failed ({type(exc).__name__})")

        # Step 4: Subscribe to notify characteristic (1911)
        # NOTE: The device may drop the connection during start_notify.
        # If that happens, we skip notifications and still attempt the write.
        notify_subscribed = False

        def notification_handler(sender: object, data: bytearray) -> None:
            ts = datetime.now().isoformat(timespec="milliseconds")
            notify_events.append(
                {
                    "timestamp": ts,
                    "length": len(data),
                    "direction": "device->host",
                }
            )
            _LOGGER.info("NOTIFY: %d bytes @ %s [content redacted]", len(data), ts)
            print(f"  << NOTIFY: {len(data)} bytes @ {ts}")

        # Write pairing data FIRST — device may disconnect during subscribe.
        # Step 5 (moved before subscribe): Write default credentials
        # The Tuya proprietary mesh pairing flow sends:
        # mesh_name (padded to 16 bytes) + mesh_password (padded to 16 bytes)
        mesh_name_bytes = TUYA_MESH_DEFAULT_NAME.encode("utf-8").ljust(16, b"\x00")
        mesh_pass_bytes = TUYA_MESH_DEFAULT_PASSWORD.encode("utf-8").ljust(16, b"\x00")
        pairing_data = mesh_name_bytes + mesh_pass_bytes

        print(f"\nWriting pairing data to {TELINK_CHAR_PAIRING}...")
        print(f"  >> WRITE: {len(pairing_data)} bytes to pairing char")
        _LOGGER.info(
            "WRITE pairing char: %d bytes [content redacted]",
            len(pairing_data),
        )

        try:
            await client.write_gatt_char(TELINK_CHAR_PAIRING, pairing_data)
            print("  Write succeeded.")
        except Exception as exc:
            print(f"  Write failed: {type(exc).__name__}")
            _LOGGER.info("Pairing write failed: %s", type(exc).__name__)

        # Step 6: Subscribe to notifications (after write)
        print(f"\nSubscribing to notifications on {TELINK_CHAR_STATUS}...")
        try:
            await client.start_notify(TELINK_CHAR_STATUS, notification_handler)
            notify_subscribed = True
            print("  Subscribed.")
        except Exception as exc:
            print(f"  Subscribe failed: {type(exc).__name__}")
            if not client.is_connected:
                print("  Device disconnected. Connection lost.")

        # Step 7: Wait for response
        print(f"\nWaiting for response ({args.listen_duration:.0f}s)...")
        await asyncio.sleep(args.listen_duration)

        # Step 8: Read characteristics after provisioning attempt
        print("\nReading characteristic state after provisioning attempt...")
        for char_uuid, label in [
            (TELINK_CHAR_STATUS, "Status (1911)"),
            (TELINK_CHAR_PAIRING, "Pairing (1913)"),
            (TELINK_CHAR_OTA, "OTA/Status (1914)"),
        ]:
            try:
                data = await client.read_gatt_char(char_uuid)
                print(f"  {label}: {len(data)} bytes [content redacted]")
            except Exception as exc:
                print(f"  {label}: read failed ({type(exc).__name__})")

        # Unsubscribe
        if notify_subscribed:
            with contextlib.suppress(Exception):
                await client.stop_notify(TELINK_CHAR_STATUS)

    # Step 8: Summary
    print(f"\n{'=' * 60}")
    print("  Provisioning Attempt Summary")
    print(f"{'=' * 60}")
    print(f"  Notifications received: {len(notify_events)}")
    for event in notify_events:
        print(f"    {event['timestamp']}: {event['length']} bytes ({event['direction']})")

    if notify_events:
        print("\n  Result: Device responded to pairing attempt.")
        print("  Next: Analyze response pattern, compare with reference projects.")
    else:
        print("\n  Result: No response from device after pairing write.")
        print("  Possible causes:")
        print("    - Credentials format incorrect (padding, encoding)")
        print("    - Device requires different auth flow")
        print("    - Device needs cloud-derived token")
        print("  Next: Capture with sniff.py, compare with Tuya app pairing.")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Attempt local provisioning of a Tuya BLE Mesh device",
    )
    parser.add_argument(
        "--mac",
        default=TARGET_DEVICE_MAC,
        help=f"Target device MAC address (default: {TARGET_DEVICE_MAC})",
    )
    parser.add_argument(
        "--scan-timeout",
        type=float,
        default=15.0,
        help="BLE scan timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=30.0,
        help="BLE connection timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--listen-duration",
        type=float,
        default=10.0,
        help="How long to wait for response in seconds (default: 10)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Show what would be done without connecting (default: True)",
    )
    parser.add_argument(
        "--no-dry-run",
        dest="dry_run",
        action="store_false",
        help="Actually attempt provisioning",
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
        asyncio.run(provision(args))
    except BLEDeviceNotFoundError as exc:
        print(f"\nError: {exc}")
        print("Try: python scripts/factory_reset.py")
        sys.exit(1)
    except (BLETimeoutError, TimeoutError) as exc:
        print(f"\nError: Connection timed out — {exc}")
        print("Try: python scripts/power_cycle.py --verify")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
