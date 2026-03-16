"""Device factory for Tuya BLE Mesh integration.

Maps device_type strings to device creation logic, replacing the if/elif
chain that was previously in __init__.py.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from custom_components.tuya_ble_mesh.const import (
    CONF_APP_KEY,
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_DEV_KEY,
    CONF_IV_INDEX,
    CONF_MESH_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_NET_KEY,
    CONF_UNICAST_OUR,
    CONF_UNICAST_TARGET,
    CONF_VENDOR_ID,
    DEFAULT_BRIDGE_PORT,
    DEFAULT_IV_INDEX,
    DEFAULT_MESH_ADDRESS,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_LOGGER = logging.getLogger(__name__)


def _create_sig_bridge_plug(
    mac_address: str,
    data: Mapping[str, Any],
    ble_device_callback: Callable[[str], Any] | None,
    ble_connect_callback: Callable[[Any], Any] | None = None,
) -> Any:
    """Create a SIG Mesh Bridge device."""
    from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.sig_mesh_bridge import (
        SIGMeshBridgeDevice,  # type: ignore[import-not-found]
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
) -> Any:
    """Create a Telink Bridge device."""
    from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.sig_mesh_bridge import (
        TelinkBridgeDevice,  # type: ignore[import-not-found]
    )

    bridge_host: str = data[CONF_BRIDGE_HOST]
    bridge_port: int = data.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)

    return TelinkBridgeDevice(
        mac_address,
        bridge_host,
        bridge_port,
    )


def _create_sig_plug(
    mac_address: str,
    data: Mapping[str, Any],
    ble_device_callback: Callable[[str], Any] | None,
    ble_connect_callback: Callable[[Any], Any] | None = None,
) -> Any:
    """Create a SIG Mesh direct device.

    Raises:
        ValueError: If required SIG Mesh keys (net_key, dev_key, app_key) are missing.
    """
    from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.secrets import DictSecretsManager  # type: ignore[import-not-found]
    from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.sig_mesh_device import SIGMeshDevice  # type: ignore[import-not-found]

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

    return SIGMeshDevice(
        mac_address,
        target_addr,
        our_addr,
        DictSecretsManager(secrets_dict),
        op_item_prefix=op_prefix,
        iv_index=iv_index,
        ble_device_callback=ble_device_callback,
        ble_connect_callback=ble_connect_callback,
    )


def _create_default_mesh_device(
    mac_address: str,
    data: Mapping[str, Any],
    ble_device_callback: Callable[[str], Any] | None,
    ble_connect_callback: Callable[[Any], Any] | None = None,
) -> Any:
    """Create a standard Tuya BLE Mesh device (light or plug)."""
    from custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.device import MeshDevice  # type: ignore[import-not-found]

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
        ble_connect_callback=ble_connect_callback,
    )


# Registry: device_type string → creator function
_DEVICE_CREATORS: dict[
    str,
    Callable[
        [str, Mapping[str, Any], Callable[[str], Any] | None, Callable[[Any], Any] | None],
        Any,
    ],
] = {
    DEVICE_TYPE_SIG_BRIDGE_PLUG: _create_sig_bridge_plug,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT: _create_telink_bridge_light,
    DEVICE_TYPE_SIG_PLUG: _create_sig_plug,
}


def create_device(
    device_type: str,
    mac_address: str,
    data: Mapping[str, Any],
    ble_device_callback: Callable[[str], Any] | None = None,
    ble_connect_callback: Callable[[Any], Any] | None = None,
) -> Any:
    """Create a mesh device instance based on device_type.

    Args:
        device_type: The device type string from config entry data.
        mac_address: BLE MAC address of the device.
        data: Config entry data dict.
        ble_device_callback: Optional callback for HA BLE proxy resolution.
        ble_connect_callback: Optional callback for HA managed BLE connections (PLAT-737).

    Returns:
        A device instance (MeshDevice, SIGMeshDevice, etc.).
    """
    creator = _DEVICE_CREATORS.get(device_type, _create_default_mesh_device)
    return creator(mac_address, data, ble_device_callback, ble_connect_callback)
