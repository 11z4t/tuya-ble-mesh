#!/usr/bin/env python3
"""GATT service explorer for Tuya BLE Mesh devices.

Connects to a BLE device, enumerates all GATT services and characteristics,
reads Device Information, listens for notifications, and classifies the
mesh variant (SIG Mesh vs Tuya proprietary vs unknown).

SECURITY: DIS values (manufacturer, model) are NOT secrets and can be printed.
Raw characteristic payloads from unknown services are logged by UUID and length
ONLY — content might contain key material.
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
    DIS_CHARACTERISTICS,
    DIS_FIRMWARE_REVISION,
    DIS_HARDWARE_REVISION,
    DIS_MANUFACTURER_NAME,
    DIS_MODEL_NUMBER,
    DIS_SOFTWARE_REVISION,
    SIG_MESH_PROVISIONING_SERVICE,
    SIG_MESH_PROXY_SERVICE,
    TARGET_DEVICE_MAC,
    TUYA_CHAR_SUFFIX_SERVICE,
    TUYA_CUSTOM_SERVICE,
)
from tuya_ble_mesh.exceptions import (
    BLEDeviceNotFoundError,
    BLETimeoutError,
)

_LOGGER = logging.getLogger(__name__)

# DIS characteristic UUID to human-readable name
DIS_NAMES: dict[str, str] = {
    DIS_MANUFACTURER_NAME: "Manufacturer",
    DIS_MODEL_NUMBER: "Model Number",
    DIS_FIRMWARE_REVISION: "Firmware Revision",
    DIS_HARDWARE_REVISION: "Hardware Revision",
    DIS_SOFTWARE_REVISION: "Software Revision",
}


# --- Pure helper functions (tested in test_explore_helpers.py) ---


def classify_mesh_variant(service_uuids: list[str]) -> str:
    """Classify the mesh variant based on discovered GATT service UUIDs.

    Detects Tuya proprietary services by either exact UUID match or by
    suffix matching (the Telink BLE stack uses a non-standard base UUID
    ``00010203-0405-0607-0809-0a0b0c0dXXXX`` with the same 1910-1914 suffixes).

    Args:
        service_uuids: List of 128-bit UUID strings (lowercase) from GATT discovery.

    Returns:
        "sig_mesh" if SIG Mesh Provisioning or Proxy service is found,
        "tuya_proprietary" if Tuya custom service (any base) is found,
        "unknown" if neither is found.
    """
    has_sig = (
        SIG_MESH_PROVISIONING_SERVICE in service_uuids or SIG_MESH_PROXY_SERVICE in service_uuids
    )
    # Match Tuya custom service by exact UUID or by suffix (Telink base UUID)
    has_tuya = TUYA_CUSTOM_SERVICE in service_uuids or any(
        uuid.endswith(TUYA_CHAR_SUFFIX_SERVICE) for uuid in service_uuids
    )

    if has_sig and has_tuya:
        # Both present — prefer SIG Mesh (better documented)
        return "sig_mesh"
    if has_sig:
        return "sig_mesh"
    if has_tuya:
        return "tuya_proprietary"
    return "unknown"


def format_report(
    *,
    mac: str,
    device_name: str,
    services: list[dict[str, object]],
    device_info: dict[str, str],
    mesh_variant: str,
    readable_chars: list[dict[str, object]],
    notifications: list[dict[str, object]],
    scan_time: str,
) -> str:
    """Format the exploration results as a structured text report.

    Args:
        mac: Device MAC address.
        device_name: Device advertised name.
        services: List of service dicts with 'uuid', 'description', 'characteristics'.
        device_info: DIS key/value pairs.
        mesh_variant: Result of classify_mesh_variant().
        readable_chars: List of readable characteristic results (uuid, length).
        notifications: List of notification events (uuid, timestamp, length).
        scan_time: ISO timestamp of when the scan was performed.

    Returns:
        Formatted multi-line report string.
    """
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  GATT Service Exploration Report")
    lines.append(f"  Device: {device_name} ({mac})")
    lines.append(f"  Scan time: {scan_time}")
    lines.append(f"  Mesh variant: {mesh_variant}")
    lines.append("=" * 60)
    lines.append("")

    # Services
    lines.append(f"GATT Services ({len(services)} found):")
    lines.append("-" * 40)
    for svc in services:
        lines.append(f"  Service: {svc['uuid']}")
        if svc.get("description"):
            lines.append(f"    Description: {svc['description']}")
        chars = svc.get("characteristics", [])
        if isinstance(chars, list):
            for char in chars:
                if isinstance(char, dict):
                    props = char.get("properties", [])
                    props_str = ", ".join(props) if isinstance(props, list) else str(props)
                    lines.append(f"    Char: {char.get('uuid', '?')}  [{props_str}]")
        lines.append("")

    # Device Information
    lines.append("Device Information:")
    lines.append("-" * 40)
    if device_info:
        for key, value in device_info.items():
            lines.append(f"  {key}: {value}")
    else:
        lines.append("  (not available)")
    lines.append("")

    # Mesh classification
    lines.append("Mesh Variant Classification:")
    lines.append("-" * 40)
    lines.append(f"  Result: {mesh_variant}")
    lines.append("")

    # Readable characteristics
    lines.append(f"Readable Characteristics ({len(readable_chars)} read):")
    lines.append("-" * 40)
    for rc in readable_chars:
        lines.append(f"  {rc.get('uuid', '?')}: {rc.get('length', '?')} bytes")
    lines.append("")

    # Notifications
    lines.append(f"Notifications ({len(notifications)} received):")
    lines.append("-" * 40)
    for n in notifications:
        uuid = n.get("uuid", "?")
        length = n.get("length", "?")
        ts = n.get("timestamp", "?")
        lines.append(f"  {uuid}: {length} bytes @ {ts}")
    lines.append("")

    return "\n".join(lines)


# --- BLE interaction functions ---


async def scan_for_device(
    mac: str,
    timeout: float = 15.0,
) -> BLEDevice:
    """Find a BLE device by MAC address.

    Args:
        mac: Target device MAC address (e.g., "DC:23:4D:21:43:A5").
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
            _LOGGER.info("Found device: %s (%s, RSSI: %d)", dev.name, dev.address, adv.rssi)

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


async def enumerate_services(client: BleakClient) -> list[dict[str, object]]:
    """Enumerate all GATT services and their characteristics.

    Args:
        client: Connected BleakClient.

    Returns:
        List of service dicts with 'uuid', 'description', and 'characteristics'.
    """
    results: list[dict[str, object]] = []

    for service in client.services:
        chars_list: list[dict[str, object]] = []
        for char in service.characteristics:
            chars_list.append(
                {
                    "uuid": char.uuid,
                    "properties": char.properties,
                    "handle": char.handle,
                }
            )

        results.append(
            {
                "uuid": service.uuid,
                "description": service.description or "",
                "characteristics": chars_list,
            }
        )

    return results


async def read_device_information(client: BleakClient) -> dict[str, str]:
    """Read Device Information Service characteristics.

    DIS values (manufacturer name, model string) are NOT secrets.

    Args:
        client: Connected BleakClient.

    Returns:
        Dict mapping human-readable names to values.
    """
    info: dict[str, str] = {}

    for char_uuid, name in DIS_NAMES.items():
        try:
            data = await client.read_gatt_char(char_uuid)
            value = data.decode("utf-8", errors="replace")
            info[name] = value
            _LOGGER.info("DIS %s: %s", name, value)
        except Exception as exc:
            _LOGGER.debug("Could not read DIS %s: %s", name, type(exc).__name__)

    return info


async def read_all_readable(client: BleakClient) -> list[dict[str, object]]:
    """Read all readable characteristics.

    SECURITY: For DIS characteristics, the decoded value is logged.
    For all other characteristics, only UUID and byte length are logged.

    Args:
        client: Connected BleakClient.

    Returns:
        List of dicts with 'uuid' and 'length' (and 'value' for DIS chars).
    """
    results: list[dict[str, object]] = []

    for service in client.services:
        for char in service.characteristics:
            if "read" in char.properties:
                try:
                    data = await client.read_gatt_char(char.uuid)
                    entry: dict[str, object] = {
                        "uuid": char.uuid,
                        "length": len(data),
                    }

                    if char.uuid in DIS_CHARACTERISTICS:
                        # DIS values are safe to display
                        entry["value"] = data.decode("utf-8", errors="replace")
                        _LOGGER.info(
                            "Read %s: %s (%d bytes)",
                            char.uuid,
                            entry["value"],
                            len(data),
                        )
                    else:
                        # Non-DIS: log UUID + length only (content may be key material)
                        _LOGGER.info(
                            "Read %s: %d bytes [content redacted]",
                            char.uuid,
                            len(data),
                        )

                    results.append(entry)
                except Exception as exc:
                    _LOGGER.debug(
                        "Could not read %s: %s",
                        char.uuid,
                        type(exc).__name__,
                    )

    return results


async def listen_for_notifications(
    client: BleakClient,
    duration: float = 10.0,
) -> list[dict[str, object]]:
    """Subscribe to all notify characteristics and collect events.

    SECURITY: Only UUID, timestamp, and byte length are logged.

    Args:
        client: Connected BleakClient.
        duration: How long to listen in seconds.

    Returns:
        List of notification events with 'uuid', 'timestamp', 'length'.
    """
    events: list[dict[str, object]] = []
    notify_chars: list[str] = []

    for service in client.services:
        for char in service.characteristics:
            if "notify" in char.properties or "indicate" in char.properties:
                notify_chars.append(char.uuid)

    if not notify_chars:
        _LOGGER.info("No notify/indicate characteristics found")
        return events

    def make_handler(uuid: str):  # type: ignore[no-untyped-def]
        def handler(sender: object, data: bytearray) -> None:
            ts = datetime.now().isoformat(timespec="milliseconds")
            events.append(
                {
                    "uuid": uuid,
                    "timestamp": ts,
                    "length": len(data),
                }
            )
            _LOGGER.info(
                "Notification from %s: %d bytes @ %s",
                uuid,
                len(data),
                ts,
            )

        return handler

    # Subscribe to all notify characteristics
    for uuid in notify_chars:
        try:
            await client.start_notify(uuid, make_handler(uuid))
            _LOGGER.info("Subscribed to notifications: %s", uuid)
        except Exception as exc:
            _LOGGER.debug("Could not subscribe to %s: %s", uuid, type(exc).__name__)

    _LOGGER.info("Listening for notifications (%.0fs)...", duration)
    await asyncio.sleep(duration)

    # Unsubscribe
    for uuid in notify_chars:
        with contextlib.suppress(Exception):
            await client.stop_notify(uuid)

    return events


async def explore(args: argparse.Namespace) -> None:
    """Main exploration flow."""
    scan_time = datetime.now().isoformat(timespec="seconds")

    # Step 1: Find device
    print(f"\n{'=' * 60}")
    print("  Tuya BLE Mesh — GATT Service Explorer")
    print(f"  Target: {args.mac}")
    print(f"  Started: {scan_time}")
    print(f"{'=' * 60}\n")

    device = await scan_for_device(args.mac, timeout=args.scan_timeout)
    device_name = device.name or "Unknown"

    # Step 2: Connect and explore
    print(f"Connecting to {device_name} ({device.address})...")

    async with BleakClient(device, timeout=args.connect_timeout) as client:
        if not client.is_connected:
            raise BLETimeoutError(f"Failed to connect to {args.mac}")

        print(f"Connected. MTU: {client.mtu_size}")

        # Enumerate services
        print("\nEnumerating GATT services...")
        services = await enumerate_services(client)
        print(f"  Found {len(services)} services")

        for svc in services:
            chars = svc.get("characteristics", [])
            char_count = len(chars) if isinstance(chars, list) else 0
            print(f"  {svc['uuid']}: {svc.get('description', '')} ({char_count} chars)")

        # Classify mesh variant
        service_uuids = [str(svc["uuid"]) for svc in services]
        mesh_variant = classify_mesh_variant(service_uuids)
        print(f"\nMesh variant: {mesh_variant}")

        # Read Device Information
        print("\nReading Device Information Service...")
        device_info = await read_device_information(client)
        for key, value in device_info.items():
            print(f"  {key}: {value}")

        # Read all readable characteristics
        print("\nReading all readable characteristics...")
        readable_chars = await read_all_readable(client)
        print(f"  Read {len(readable_chars)} characteristics")

        # Listen for notifications
        print(f"\nListening for notifications ({args.listen_duration:.0f}s)...")
        notifications = await listen_for_notifications(client, duration=args.listen_duration)
        print(f"  Received {len(notifications)} notifications")

    # Step 3: Generate report
    report = format_report(
        mac=args.mac,
        device_name=device_name,
        services=services,
        device_info=device_info,
        mesh_variant=mesh_variant,
        readable_chars=readable_chars,
        notifications=notifications,
        scan_time=scan_time,
    )

    print(f"\n{report}")

    # Save report to file if requested
    if args.output:
        output_path = pathlib.Path(args.output)
        output_path.write_text(report, encoding="utf-8")
        print(f"Report saved to: {output_path}")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Explore GATT services on a Tuya BLE Mesh device",
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
        default=15.0,
        help="BLE connection timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--listen-duration",
        type=float,
        default=10.0,
        help="How long to listen for notifications in seconds (default: 10)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Save report to file",
    )

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    try:
        asyncio.run(explore(args))
    except BLEDeviceNotFoundError as exc:
        print(f"\nError: {exc}")
        print("Make sure the device is powered on and in range.")
        print("Try running: python scripts/factory_reset.py")
        sys.exit(1)
    except (BLETimeoutError, TimeoutError) as exc:
        print(f"\nError: Connection timed out — {exc}")
        print("The device may not accept GATT connections in this state.")
        print("Try: python scripts/factory_reset.py  (to reset to pairing mode)")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nScan interrupted.")
        sys.exit(0)


if __name__ == "__main__":
    main()
