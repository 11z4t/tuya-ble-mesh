"""Device factory for Tuya BLE Mesh integration.

Maps device_type strings to device creation logic, replacing the if/elif
chain that was previously in __init__.py.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypeAlias, Union

from custom_components.tuya_ble_mesh.const import (
    CONF_APP_KEY,
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_DEV_KEY,
    CONF_DEVICE_TYPE,
    CONF_ELEMENT_COUNT,
    CONF_IV_INDEX,
    CONF_MAC_ADDRESS,
    CONF_MESH_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_MODEL_ELEMENTS,
    CONF_NET_KEY,
    CONF_TEMP_MAX_K,
    CONF_TEMP_MIN_K,
    CONF_UNICAST_OUR,
    CONF_UNICAST_TARGET,
    CONF_VENDOR_ID,
    DEFAULT_BRIDGE_PORT,
    DEFAULT_IV_INDEX,
    DEFAULT_MESH_ADDRESS,
    DEFAULT_SIG_TEMP_MAX_K,
    DEFAULT_SIG_TEMP_MIN_K,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_LIGHT,
    DEVICE_TYPE_SIG_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from tuya_ble_mesh.device import MeshDevice
    from tuya_ble_mesh.sig_mesh_bridge import (
        SIGMeshBridgeDevice,
        TelinkBridgeDevice,
    )
    from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice
    from tuya_ble_mesh.sig_mesh_light import SIGMeshLight
    from tuya_ble_mesh.sig_mesh_node import SIGMeshLightNode, SIGMeshNode

# Union type alias for all mesh device types returned by device_factory
AnyMeshDevice: TypeAlias = Union[
    "MeshDevice",
    "SIGMeshDevice",
    "SIGMeshBridgeDevice",
    "TelinkBridgeDevice",
]

_LOGGER = logging.getLogger(__name__)


def _create_sig_bridge_plug(
    mac_address: str,
    data: Mapping[str, Any],
    ble_device_callback: Callable[[str], Any] | None,
    ble_connect_callback: Callable[[Any], Any] | None = None,
) -> SIGMeshBridgeDevice:
    """Create a SIG Mesh Bridge device."""
    from tuya_ble_mesh.sig_mesh_bridge import (
        SIGMeshBridgeDevice,
    )

    target_addr = int(data.get(CONF_UNICAST_TARGET, "00B0"), 16)
    bridge_host: str = data[CONF_BRIDGE_HOST]
    bridge_port: int = data.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)

    return SIGMeshBridgeDevice(
        mac_address,
        target_addr,
        bridge_host,
        bridge_port,
    )


def _create_telink_bridge_light(
    mac_address: str,
    data: Mapping[str, Any],
    ble_device_callback: Callable[[str], Any] | None,
    ble_connect_callback: Callable[[Any], Any] | None = None,
) -> TelinkBridgeDevice:
    """Create a Telink Bridge device."""
    from tuya_ble_mesh.sig_mesh_bridge import (
        TelinkBridgeDevice,
    )

    bridge_host: str = data[CONF_BRIDGE_HOST]
    bridge_port: int = data.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)

    return TelinkBridgeDevice(
        mac_address,
        bridge_host,
        bridge_port,
    )


def _sig_direct_args(
    mac_address: str, data: Mapping[str, Any]
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Build the shared SIGMeshDevice constructor (args, kwargs) from entry data.

    Raises:
        ValueError: If required SIG Mesh keys (net_key, dev_key, app_key) are missing.
    """
    from tuya_ble_mesh.secrets import DictSecretsManager

    # PLAT-739: Validate required keys are present
    net_key = data.get(CONF_NET_KEY, "")
    dev_key = data.get(CONF_DEV_KEY, "")
    app_key = data.get(CONF_APP_KEY, "")

    missing_keys = []
    if not net_key:
        missing_keys.append(CONF_NET_KEY)
    if not dev_key:
        missing_keys.append(CONF_DEV_KEY)
    if not app_key:
        missing_keys.append(CONF_APP_KEY)

    if missing_keys:
        raise ValueError(
            f"SIG Mesh device {mac_address} config entry is missing required keys: "
            f"{', '.join(missing_keys)}. Device must be provisioned first."
        )

    target_addr = int(data.get(CONF_UNICAST_TARGET, "00B0"), 16)
    our_addr = int(data.get(CONF_UNICAST_OUR, "0001"), 16)
    iv_index: int = data.get(CONF_IV_INDEX, DEFAULT_IV_INDEX)

    target_hex = f"{target_addr:04x}"
    op_prefix = "cfg"
    secrets_dict = {
        f"{op_prefix}-net-key/password": net_key,
        f"{op_prefix}-dev-key-{target_hex}/password": dev_key,
        f"{op_prefix}-app-key/password": app_key,
    }
    args = (mac_address, target_addr, our_addr, DictSecretsManager(secrets_dict))
    return args, {"op_item_prefix": op_prefix, "iv_index": iv_index}


def _create_sig_plug(
    mac_address: str,
    data: Mapping[str, Any],
    ble_device_callback: Callable[[str], Any] | None,
    ble_connect_callback: Callable[[Any], Any] | None = None,
) -> SIGMeshDevice:
    """Create a SIG Mesh direct device.

    ``ble_connect_callback`` is accepted for factory-signature compatibility but
    unused: SIGMeshDevice connects via bleak-retry-connector itself whenever a
    ``ble_device_callback`` (HA Bluetooth) is supplied.

    Raises:
        ValueError: If required SIG Mesh keys (net_key, dev_key, app_key) are missing.
    """
    from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice

    args, kwargs = _sig_direct_args(mac_address, data)
    return SIGMeshDevice(*args, **kwargs, ble_device_callback=ble_device_callback)


def _create_sig_light(
    mac_address: str,
    data: Mapping[str, Any],
    ble_device_callback: Callable[[str], Any] | None,
    ble_connect_callback: Callable[[Any], Any] | None = None,
) -> SIGMeshLight:
    """Create a SIG Mesh light (OnOff / Lightness / CTL / HSL).

    Raises:
        ValueError: If required SIG Mesh keys are missing.
    """
    from tuya_ble_mesh.sig_mesh_light import SIGMeshLight

    model_elements, temp_range = _light_options(data)
    args, kwargs = _sig_direct_args(mac_address, data)
    return SIGMeshLight(
        *args,
        **kwargs,
        ble_device_callback=ble_device_callback,
        model_elements=model_elements,
        temp_range_k=temp_range,
    )


def _light_options(
    data: Mapping[str, Any],
) -> tuple[dict[int, int], tuple[int, int] | None]:
    """Parse model->element map and device-reported CTL range from entry data."""
    raw_elements: Mapping[str, Any] = data.get(CONF_MODEL_ELEMENTS) or {}
    model_elements: dict[int, int] = {}
    for model_hex, index in raw_elements.items():
        try:
            model_elements[int(model_hex, 16)] = int(index)
        except (TypeError, ValueError):
            _LOGGER.debug("Ignoring bad model element entry %r=%r", model_hex, index)

    # Only a device-reported range is passed; without one the light scales
    # its default display range onto the full mesh range (Tuya behaviour).
    temp_range: tuple[int, int] | None = None
    if CONF_TEMP_MIN_K in data and CONF_TEMP_MAX_K in data:
        temp_range = (
            int(data.get(CONF_TEMP_MIN_K, DEFAULT_SIG_TEMP_MIN_K)),
            int(data.get(CONF_TEMP_MAX_K, DEFAULT_SIG_TEMP_MAX_K)),
        )
    return model_elements, temp_range


def create_network_node(
    network: Any,
    data: Mapping[str, Any],
    rssi_provider: Callable[[str], int | None] | None = None,
) -> SIGMeshNode | SIGMeshLightNode:
    """Create a node view on a shared ``MeshNetwork`` from node subentry data.

    Raises:
        ValueError: If the subentry lacks a MAC, unicast address or device key.
    """
    from tuya_ble_mesh.sig_mesh_node import SIGMeshLightNode, SIGMeshNode

    mac = str(data.get(CONF_MAC_ADDRESS, "")).upper()
    dev_key = str(data.get(CONF_DEV_KEY, ""))
    unicast_hex = data.get(CONF_UNICAST_TARGET)
    if not mac or not dev_key or not unicast_hex:
        msg = "Node subentry is missing mac_address, dev_key or unicast_target"
        raise ValueError(msg)
    unicast = int(str(unicast_hex), 16)
    element_count = int(data.get(CONF_ELEMENT_COUNT, 1) or 1)
    if data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_SIG_LIGHT:
        model_elements, temp_range = _light_options(data)
        return SIGMeshLightNode(
            mac,
            network,
            unicast,
            dev_key,
            element_count=element_count,
            model_elements=model_elements,
            temp_range_k=temp_range,
            rssi_provider=rssi_provider,
        )
    return SIGMeshNode(
        mac, network, unicast, dev_key, element_count=element_count, rssi_provider=rssi_provider
    )


def _create_default_mesh_device(
    mac_address: str,
    data: Mapping[str, Any],
    ble_device_callback: Callable[[str], Any] | None,
    ble_connect_callback: Callable[[Any], Any] | None = None,
) -> MeshDevice:
    """Create a standard Tuya BLE Mesh device (light or plug).

    Note: MeshDevice does not support ble_connect_callback parameter.
    Only ble_device_callback is passed through.
    """
    from tuya_ble_mesh.device import MeshDevice

    mesh_name: str = data[CONF_MESH_NAME]
    mesh_password: str = data[CONF_MESH_PASSWORD]
    vendor_id_hex: str = data.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID)
    vendor_id_int = int(vendor_id_hex, 16)
    vendor_id_bytes = vendor_id_int.to_bytes(2, "little")
    mesh_addr: int = data.get(CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS)

    return MeshDevice(
        mac_address,
        mesh_name.encode(),
        mesh_password.encode(),
        mesh_id=mesh_addr,
        vendor_id=vendor_id_bytes,
        ble_device_callback=ble_device_callback,
    )


# Registry: device_type string → creator function
_DEVICE_CREATORS: dict[
    str,
    Callable[
        [str, Mapping[str, Any], Callable[[str], Any] | None, Callable[[Any], Any] | None],
        AnyMeshDevice,
    ],
] = {
    DEVICE_TYPE_SIG_BRIDGE_PLUG: _create_sig_bridge_plug,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT: _create_telink_bridge_light,
    DEVICE_TYPE_SIG_PLUG: _create_sig_plug,
    DEVICE_TYPE_SIG_LIGHT: _create_sig_light,
}


def create_device(
    device_type: str,
    mac_address: str,
    data: Mapping[str, Any],
    ble_device_callback: Callable[[str], Any] | None = None,
    ble_connect_callback: Callable[[Any], Any] | None = None,
) -> AnyMeshDevice:
    """Create a mesh device instance based on device_type.

    Args:
        device_type: The device type string from config entry data.
        mac_address: BLE MAC address of the device.
        data: Config entry data dict.
        ble_device_callback: Optional callback for HA BLE proxy resolution.
        ble_connect_callback: Optional callback for HA managed BLE connections (PLAT-737).

    Returns:
        A device instance (MeshDevice, SIGMeshDevice, SIGMeshBridgeDevice, or TelinkBridgeDevice).
    """
    creator = _DEVICE_CREATORS.get(device_type, _create_default_mesh_device)
    return creator(mac_address, data, ble_device_callback, ble_connect_callback)
