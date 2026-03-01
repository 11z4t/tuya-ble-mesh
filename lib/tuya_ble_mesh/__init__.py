"""Tuya BLE Mesh library for local BLE mesh device control.

Fully local BLE mesh control — no cloud dependency.

Public API::

    from tuya_ble_mesh import MeshDevice

    async with MeshDevice("DC:23:4D:21:43:A5", b"out_of_mesh", b"123456") as dev:
        await dev.send_power(True)
        await dev.send_brightness(100)

Modules:
    device — High-level device command interface
    provisioner — Pairing and provisioning
    scanner — BLE device discovery
    protocol — Packet encoding/decoding (internal)
    crypto — Cryptographic operations (internal)
    dps — Device profile loading
    power — Shelly power control
    secrets — 1Password integration
    exceptions — Exception hierarchy
    const — Protocol constants
"""

from tuya_ble_mesh.device import MeshDevice
from tuya_ble_mesh.dps import DeviceProfile, load_profile, load_profile_by_model
from tuya_ble_mesh.exceptions import (
    AuthenticationError,
    ConnectionError,
    CryptoError,
    DeviceNotFoundError,
    MalmbergsBTError,
    PowerControlError,
    ProtocolError,
    ProvisioningError,
    SecretAccessError,
    TimeoutError,
    TuyaBLEMeshError,
)
from tuya_ble_mesh.provisioner import provision
from tuya_ble_mesh.scanner import (
    DiscoveredDevice,
    find_device_by_mac,
    scan_for_devices,
    scan_for_tuya_devices,
)

__all__ = [
    "AuthenticationError",
    "ConnectionError",
    "CryptoError",
    "DeviceNotFoundError",
    "DeviceProfile",
    "DiscoveredDevice",
    "MalmbergsBTError",
    "MeshDevice",
    "PowerControlError",
    "ProtocolError",
    "ProvisioningError",
    "SecretAccessError",
    "TimeoutError",
    "TuyaBLEMeshError",
    "find_device_by_mac",
    "load_profile",
    "load_profile_by_model",
    "provision",
    "scan_for_devices",
    "scan_for_tuya_devices",
]
