"""BLE scanner for Tuya / Telink mesh devices.

Discovers BLE devices using bleak and identifies Tuya mesh devices
by name pattern or service UUID matching.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from tuya_ble_mesh.const import (
    TELINK_BASE_UUID_PREFIX,
    TUYA_MESH_NAME_PATTERNS,
    TUYA_MESH_SERVICE_UUID,
)
from tuya_ble_mesh.exceptions import DeviceNotFoundError
from tuya_ble_mesh.logging_context import MeshLogAdapter

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

_DEFAULT_SCAN_TIMEOUT = 15.0


@dataclass(frozen=True)
class DiscoveredDevice:
    """A discovered BLE device with advertisement data."""

    name: str
    address: str
    rssi: int
    service_uuids: tuple[str, ...]
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)
    is_tuya_mesh: bool = False
    is_telink_mesh: bool = False


def is_tuya_mesh_device(
    name: str | None,
    service_uuids: list[str] | None,
) -> bool:
    """Check if a device is likely a Tuya BLE mesh device.

    Matches on name pattern or service UUID.

    Args:
        name: Device advertised name (may be None).
        service_uuids: Advertised service UUIDs.

    Returns:
        True if the device matches Tuya mesh patterns.
    """
    if name:
        for pattern in TUYA_MESH_NAME_PATTERNS:
            if name.lower().startswith(pattern.lower()):
                return True

    return bool(service_uuids and TUYA_MESH_SERVICE_UUID in service_uuids)


def is_telink_mesh_device(service_uuids: list[str] | None) -> bool:
    """Check if a device uses Telink BLE mesh UUIDs.

    Args:
        service_uuids: Advertised service UUIDs.

    Returns:
        True if any UUID uses the Telink base prefix.
    """
    if not service_uuids:
        return False
    return any(u.startswith(TELINK_BASE_UUID_PREFIX) for u in service_uuids)


def _make_discovered(
    device: BLEDevice,
    adv: AdvertisementData,
) -> DiscoveredDevice:
    """Convert bleak device + advertisement to DiscoveredDevice."""
    name = device.name or ""
    uuids = list(adv.service_uuids or [])

    return DiscoveredDevice(
        name=name,
        address=device.address,
        rssi=adv.rssi,
        service_uuids=tuple(uuids),
        manufacturer_data=dict(adv.manufacturer_data or {}),
        is_tuya_mesh=is_tuya_mesh_device(name, uuids),
        is_telink_mesh=is_telink_mesh_device(uuids),
    )


async def scan_for_devices(
    timeout: float = _DEFAULT_SCAN_TIMEOUT,
) -> list[DiscoveredDevice]:
    """Scan for all BLE devices and classify Tuya mesh ones.

    Args:
        timeout: Scan duration in seconds.

    Returns:
        List of discovered devices, sorted by RSSI (strongest first).
    """
    _LOGGER.info("Starting BLE scan (%.1fs)", timeout)

    devices_map: dict[str, DiscoveredDevice] = {}

    def callback(device: BLEDevice, adv: AdvertisementData) -> None:
        discovered = _make_discovered(device, adv)
        existing = devices_map.get(device.address)
        if existing is None or discovered.rssi > existing.rssi:
            devices_map[device.address] = discovered

    async with BleakScanner(detection_callback=callback):
        await asyncio.sleep(timeout)

    result = sorted(devices_map.values(), key=lambda d: d.rssi, reverse=True)
    _LOGGER.info("Scan complete: %d devices found", len(result))
    return result


async def scan_for_tuya_devices(
    timeout: float = _DEFAULT_SCAN_TIMEOUT,
) -> list[DiscoveredDevice]:
    """Scan and return only Tuya mesh devices.

    Args:
        timeout: Scan duration in seconds.

    Returns:
        List of Tuya mesh devices, sorted by RSSI.
    """
    all_devices = await scan_for_devices(timeout)
    return [d for d in all_devices if d.is_tuya_mesh or d.is_telink_mesh]


async def find_device_by_mac(
    mac: str,
    timeout: float = _DEFAULT_SCAN_TIMEOUT,
) -> DiscoveredDevice:
    """Scan for a specific device by MAC address.

    Args:
        mac: BLE MAC address (e.g. ``DC:23:4D:21:43:A5``).
        timeout: Scan duration in seconds.

    Returns:
        The discovered device.

    Raises:
        DeviceNotFoundError: If the device is not found within timeout.
    """
    mac_upper = mac.upper()
    _LOGGER.info("Scanning for device %s (%.1fs)", mac_upper, timeout)

    all_devices = await scan_for_devices(timeout)
    for device in all_devices:
        if device.address.upper() == mac_upper:
            _LOGGER.info("Found %s at RSSI %d dBm", mac_upper, device.rssi)
            return device

    msg = f"Device {mac_upper} not found after {timeout}s scan"
    raise DeviceNotFoundError(msg)


def mac_to_bytes(mac: str) -> bytes:
    """Convert a MAC address string to 6 bytes.

    Args:
        mac: MAC address (e.g. ``DC:23:4D:21:43:A5``).

    Returns:
        6-byte MAC address.

    Raises:
        ProtocolError: If the MAC format is invalid.
    """
    from tuya_ble_mesh.exceptions import ProtocolError

    if not mac:
        msg = "MAC address cannot be empty"
        raise ProtocolError(msg)

    parts = mac.split(":")
    if len(parts) != 6:
        msg = f"Invalid MAC format: {mac}"
        raise ProtocolError(msg)
    try:
        return bytes(int(p, 16) for p in parts)
    except ValueError:
        msg = f"Invalid MAC hex values: {mac}"
        raise ProtocolError(msg) from None
