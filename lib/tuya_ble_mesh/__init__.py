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

from tuya_ble_mesh.connection import ConnectionState
from tuya_ble_mesh.device import MeshDevice
from tuya_ble_mesh.dps import DeviceProfile, list_profiles, load_profile, load_profile_by_model
from tuya_ble_mesh.exceptions import (
    AuthenticationError,
    CommandExpiredError,
    CommandQueueFullError,
    ConnectionError,
    CryptoError,
    DeviceNotFoundError,
    DisconnectedError,
    MalmbergsBTError,
    PowerControlError,
    ProtocolError,
    ProvisioningError,
    SecretAccessError,
    SIGMeshError,
    SIGMeshKeyError,
    TimeoutError,
    TuyaBLEMeshError,
)
from tuya_ble_mesh.power import ShellyPowerController
from tuya_ble_mesh.protocol import StatusResponse
from tuya_ble_mesh.provisioner import provision
from tuya_ble_mesh.scanner import (
    DiscoveredDevice,
    find_device_by_mac,
    scan_for_devices,
    scan_for_tuya_devices,
)
from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice
from tuya_ble_mesh.sig_mesh_protocol import (
    AccessMessage,
    MeshKeys,
    NetworkPDU,
    ProxyPDU,
    config_appkey_add,
    config_composition_get,
    config_model_app_bind,
    decrypt_access_payload,
    decrypt_network_pdu,
    encrypt_network_pdu,
    format_status_response,
    generic_onoff_get,
    generic_onoff_set,
    make_access_segmented,
    make_access_unsegmented,
    make_proxy_pdu,
    parse_access_opcode,
    parse_proxy_pdu,
)

__all__ = [
    "AccessMessage",
    "AuthenticationError",
    "CommandExpiredError",
    "CommandQueueFullError",
    "ConnectionError",
    "ConnectionState",
    "CryptoError",
    "DeviceNotFoundError",
    "DeviceProfile",
    "DisconnectedError",
    "DiscoveredDevice",
    "MalmbergsBTError",
    "MeshDevice",
    "MeshKeys",
    "NetworkPDU",
    "PowerControlError",
    "ProtocolError",
    "ProvisioningError",
    "ProxyPDU",
    "SIGMeshDevice",
    "SIGMeshError",
    "SIGMeshKeyError",
    "SecretAccessError",
    "ShellyPowerController",
    "StatusResponse",
    "TimeoutError",
    "TuyaBLEMeshError",
    "config_appkey_add",
    "config_composition_get",
    "config_model_app_bind",
    "decrypt_access_payload",
    "decrypt_network_pdu",
    "encrypt_network_pdu",
    "find_device_by_mac",
    "format_status_response",
    "generic_onoff_get",
    "generic_onoff_set",
    "list_profiles",
    "load_profile",
    "load_profile_by_model",
    "make_access_segmented",
    "make_access_unsegmented",
    "make_proxy_pdu",
    "parse_access_opcode",
    "parse_proxy_pdu",
    "provision",
    "scan_for_devices",
    "scan_for_tuya_devices",
]
