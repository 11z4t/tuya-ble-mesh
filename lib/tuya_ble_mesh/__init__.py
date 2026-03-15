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

from __future__ import annotations

from tuya_ble_mesh.connection import ConnectionState
from tuya_ble_mesh.device import MeshDevice
from tuya_ble_mesh.device_protocol import MeshDeviceProtocol
from tuya_ble_mesh.dps import DeviceProfile, list_profiles, load_profile, load_profile_by_model
from tuya_ble_mesh.exceptions import (
    AuthenticationError,
    CommandExpiredError,
    CommandQueueFullError,
    CorrelationConflictError,
    CryptoError,
    DeviceNotFoundError,
    DisconnectedError,
    InvalidRequestError,
    InvalidResultError,
    MalformedPacketError,
    MalmbergsBTError,
    MeshConnectionError,
    MeshTimeoutError,
    PowerControlError,
    ProtocolError,
    ProvisioningError,
    SecretAccessError,
    SIGMeshError,
    SIGMeshKeyError,
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
    TUYA_CMD_TIMESTAMP_SYNC,
    TUYA_VENDOR_OPCODE,
    TUYA_VENDOR_WRITE_UNACK,
    AccessMessage,
    CompositionData,
    MeshKeys,
    NetworkPDU,
    ProxyPDU,
    SegmentHeader,
    TuyaVendorDP,
    TuyaVendorFrame,
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
    parse_composition_data,
    parse_proxy_pdu,
    parse_segment_header,
    parse_tuya_vendor_dps,
    parse_tuya_vendor_frame,
    reassemble_and_decrypt_segments,
    tuya_vendor_timestamp_response,
)

__all__ = [
    "TUYA_CMD_TIMESTAMP_SYNC",
    "TUYA_VENDOR_OPCODE",
    "TUYA_VENDOR_WRITE_UNACK",
    "AccessMessage",
    "AuthenticationError",
    "CommandExpiredError",
    "CommandQueueFullError",
    "CompositionData",
    "ConnectionState",
    "CorrelationConflictError",
    "CryptoError",
    "DeviceNotFoundError",
    "DeviceProfile",
    "DisconnectedError",
    "DiscoveredDevice",
    "InvalidRequestError",
    "InvalidResultError",
    "MalformedPacketError",
    "MalmbergsBTError",
    "MeshConnectionError",
    "MeshDevice",
    "MeshDeviceProtocol",
    "MeshKeys",
    "MeshTimeoutError",
    "NetworkPDU",
    "PowerControlError",
    "ProtocolError",
    "ProvisioningError",
    "ProxyPDU",
    "SIGMeshDevice",
    "SIGMeshError",
    "SIGMeshKeyError",
    "SecretAccessError",
    "SegmentHeader",
    "ShellyPowerController",
    "StatusResponse",
    "TuyaBLEMeshError",
    "TuyaVendorDP",
    "TuyaVendorFrame",
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
    "parse_composition_data",
    "parse_proxy_pdu",
    "parse_segment_header",
    "parse_tuya_vendor_dps",
    "parse_tuya_vendor_frame",
    "provision",
    "reassemble_and_decrypt_segments",
    "scan_for_devices",
    "scan_for_tuya_devices",
    "tuya_vendor_timestamp_response",
]
