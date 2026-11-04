"""SIG Mesh provisioning for Tuya BLE Mesh config flow.
Handles:
- SIG Mesh plug and light provisioning via PB-GATT
- SIG Mesh bridge configuration
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import voluptuous as vol

if TYPE_CHECKING:
    from homeassistant.data_entry_flow import FlowResult
from custom_components.tuya_ble_mesh.const import (
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_ELEMENT_COUNT,
    CONF_MODEL_ELEMENTS,
    CONF_SEQ_START,
    CONF_TEMP_MAX_K,
    CONF_TEMP_MIN_K,
    CONF_UNICAST_TARGET,
    DEFAULT_BRIDGE_PORT,
    DEFAULT_IV_INDEX,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_LIGHT,
    DEVICE_TYPE_SIG_PLUG,
)

_LOGGER = logging.getLogger(__name__)
# Unicast addresses used during provisioning
_UNICAST_PROVISIONER = 0x0001
_UNICAST_DEVICE_DEFAULT = 0x00B0
# GenericOnOff Server SIG Model ID
_MODEL_GENERIC_ONOFF_SERVER = 0x1000
# Overall PB-GATT provisioning budget. Weak links (e.g. bulbs in metal fixtures)
# can need several 15 s connect attempts before the exchange itself starts.
_PROVISION_TIMEOUT = 180.0
# Seconds to wait for device to reboot as Proxy Service after provisioning
_POST_PROV_REBOOT_DELAY = 6.0


class NodeConfigurationError(Exception):
    """Device was provisioned but post-provisioning configuration failed.

    The device now holds keys for a network HA does not keep, so it must be
    factory reset before trying again.
    """


@dataclass(frozen=True)
class ProvisionedNode:
    """Result of provisioning + configuring a SIG Mesh node (keys as hex)."""

    net_key: str
    dev_key: str
    app_key: str
    unicast: int
    num_elements: int
    model_elements: dict[str, int] = field(default_factory=dict)
    temp_range_k: tuple[int, int] | None = None
    next_seq: int = 0

    def entry_data(self) -> dict[str, Any]:
        """Config entry fields (beyond MAC/device type) for this node."""
        data: dict[str, Any] = {
            "unicast_target": f"{self.unicast:04X}",
            "unicast_our": f"{_UNICAST_PROVISIONER:04X}",
            "iv_index": DEFAULT_IV_INDEX,
            "net_key": self.net_key,
            "dev_key": self.dev_key,
            "app_key": self.app_key,
            CONF_ELEMENT_COUNT: self.num_elements,
            CONF_SEQ_START: self.next_seq,
        }
        if self.model_elements:
            data[CONF_MODEL_ELEMENTS] = dict(self.model_elements)
        if self.temp_range_k is not None:
            data[CONF_TEMP_MIN_K], data[CONF_TEMP_MAX_K] = self.temp_range_k
        return data


def ha_ble_callbacks(hass: Any) -> tuple[Any, Any]:
    """Return (ble_device_callback, ble_connect_callback) routed via HA bluetooth."""
    from bleak import BleakClient
    from bleak_retry_connector import establish_connection
    from homeassistant.components import bluetooth as ha_bluetooth

    # HA Bluetooth callbacks -- use retry-connector to avoid HA warning
    # NOTE: Works with ESPHome BLE proxies. If HA has no local adapter but has
    # ESPHome BLE proxies, devices discovered by proxies will be in HA's bluetooth
    # registry and establish_connection will route traffic via the proxy.
    def _ble_device_cb(address: str) -> Any:
        """Look up BLEDevice via HA bluetooth registry.

        Tries connectable=True first (preferred for direct BLE connections).
        Falls back to connectable=False for devices seen only via passive scan
        — bleak-retry-connector will handle the actual connection attempt.
        """
        device = ha_bluetooth.async_ble_device_from_address(hass, address.upper(), connectable=True)
        if device is None:
            device = ha_bluetooth.async_ble_device_from_address(
                hass, address.upper(), connectable=False
            )
            if device is not None:
                _LOGGER.info(
                    "BLEDevice %s found via passive scan only (connectable=False) — "
                    "will attempt connection anyway",
                    address,
                )
            else:
                _LOGGER.warning(
                    "BLEDevice not found in HA bluetooth registry for %s. "
                    "Ensure device is in range of a BLE adapter or ESPHome BLE proxy.",
                    address,
                )
        else:
            _LOGGER.debug("Found BLEDevice for %s (connectable): %s", address, device)
        return device

    async def _ble_connect_cb(ble_device: Any) -> BleakClient:
        """Connect via bleak-retry-connector with service caching and stale cleanup.
        PLAT-737: Use BleakClientWithServiceCache + close_stale_connections
        to prevent "Busy" adapter errors during provisioning.
        """
        from bleak_retry_connector import (
            BleakClientWithServiceCache,
            close_stale_connections_by_address,
        )

        # Clean up stale connections before connecting
        await close_stale_connections_by_address(ble_device.address)
        # PLAT-760: use_services_cache=False forces fresh GATT service discovery.
        # Old cache may contain Proxy (0x1828) services from before factory-reset;
        # provisioning requires seeing Provisioning (0x1827) after reset.
        return await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            f"Provisioning {ble_device.address}",
            max_attempts=5,
            use_services_cache=False,
        )

    return _ble_device_cb, _ble_connect_cb


async def run_provision(
    hass: Any, mac: str, device_type: str = DEVICE_TYPE_SIG_PLUG
) -> ProvisionedNode:
    """Generate keys, provision the device, then configure it for use.

    Phase 1: PB-GATT provisioning (Service 0x1827).
    Phase 2: Wait for device to reboot into Proxy Service (0x1828).
    Phase 3: AppKey Add, Composition Data Get, bind the AppKey to every server
        model (lights: also query the colour temperature range and enable
        status publication to HA).

    Args:
        hass: Home Assistant instance.
        mac: BLE MAC address of the unprovisioned device.
        device_type: DEVICE_TYPE_SIG_PLUG or DEVICE_TYPE_SIG_LIGHT.

    Returns:
        ProvisionedNode with keys and discovered model layout.

    Raises:
        ProvisioningError: If PB-GATT provisioning fails.
        TimeoutError: If provisioning times out.
        NodeConfigurationError: If Phase 3 fails (device needs factory reset).
    """
    from tuya_ble_mesh.secrets import DictSecretsManager
    from tuya_ble_mesh.sig_mesh_light import (
        SIGMeshLight,
        configure_sig_node,
        model_elements_from_composition,
    )
    from tuya_ble_mesh.sig_mesh_provisioner import (
        SIGMeshProvisioner,
    )

    is_light = device_type == DEVICE_TYPE_SIG_LIGHT

    # Generate fresh random keys (SECURITY: never logged)
    net_key = os.urandom(16)
    app_key = os.urandom(16)
    _LOGGER.info(
        "Auto-provisioning SIG Mesh %s %s (unicast=0x%04X)",
        "light" if is_light else "plug",
        mac,
        _UNICAST_DEVICE_DEFAULT,
    )

    _ble_device_cb, _ble_connect_cb = ha_ble_callbacks(hass)

    # Phase 1: PB-GATT provisioning
    provisioner = SIGMeshProvisioner(
        net_key=net_key,
        app_key=app_key,
        unicast_addr=_UNICAST_DEVICE_DEFAULT,
        iv_index=DEFAULT_IV_INDEX,
        ble_device_callback=_ble_device_cb,
        ble_connect_callback=_ble_connect_cb,
    )
    try:
        result = await asyncio.wait_for(provisioner.provision(mac), timeout=_PROVISION_TIMEOUT)
    except TimeoutError:
        raise TimeoutError(f"Provisioning timed out after {_PROVISION_TIMEOUT:.0f}s") from None
    _LOGGER.info(
        "PB-GATT provisioning succeeded for %s (%d elements)",
        mac,
        result.num_elements,
    )
    # Phase 2: Wait for device to reboot and switch to Proxy Service
    _LOGGER.info("Waiting %.0fs for %s to reboot as Proxy Service...", _POST_PROV_REBOOT_DELAY, mac)
    await asyncio.sleep(_POST_PROV_REBOOT_DELAY)
    # Phase 3: Post-provisioning config via GATT Proxy
    op_prefix = "cfg"
    target_hex = f"{_UNICAST_DEVICE_DEFAULT:04x}"
    dev_key_name = f"{op_prefix}-dev-key-{target_hex}/password"
    secrets_dict = {
        f"{op_prefix}-net-key/password": net_key.hex(),
        dev_key_name: result.dev_key.hex(),
        f"{op_prefix}-app-key/password": app_key.hex(),
    }
    device = SIGMeshLight(
        mac,
        _UNICAST_DEVICE_DEFAULT,
        _UNICAST_PROVISIONER,
        DictSecretsManager(secrets_dict),
        op_item_prefix=op_prefix,
        iv_index=DEFAULT_IV_INDEX,
        ble_device_callback=_ble_device_cb,
    )
    model_elements: dict[str, int] = {}
    temp_range: tuple[int, int] | None = None
    try:
        # Fresh service discovery: cached services still describe 0x1827
        await device.connect(timeout=20.0, max_retries=5, fresh_services=True)
        node = await configure_sig_node(
            device,
            app_key,
            fallback_models=(_MODEL_GENERIC_ONOFF_SERVER,),
            publish_to=_UNICAST_PROVISIONER if is_light else None,
        )
        if node.failed_models:
            _LOGGER.warning(
                "Model App Bind failed for %s on %d model(s): %s",
                mac,
                len(node.failed_models),
                ", ".join(f"elem{i}:0x{m:04X}" for i, m in node.failed_models),
            )
        if node.composition is not None:
            model_elements = {
                f"{m:04x}": i for m, i in model_elements_from_composition(node.composition).items()
            }
        if is_light:
            try:
                reported = await device.get_temperature_range()
                # Store only a range the device actually reported
                if not getattr(device, "_scale_temperature", True):
                    temp_range = reported
            except Exception:
                _LOGGER.info("Temperature range query failed for %s; using default", mac)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _LOGGER.warning("Post-provisioning config failed for %s", mac, exc_info=True)
        raise NodeConfigurationError(str(exc)) from exc
    finally:
        next_seq = int(device.get_seq())
        await device.disconnect()
    return ProvisionedNode(
        net_key=net_key.hex(),
        dev_key=result.dev_key.hex(),
        app_key=app_key.hex(),
        unicast=_UNICAST_DEVICE_DEFAULT,
        num_elements=result.num_elements,
        model_elements=model_elements,
        temp_range_k=temp_range,
        next_seq=next_seq,
    )


async def async_step_sig_plug(flow: Any, user_input: dict[str, Any] | None) -> FlowResult:
    """Handle SIG Mesh plug -- auto-provisions and generates all keys.

    The device is provisioned via PB-GATT (Service UUID 0x1827).
    A random network key and device key are established via a secure key exchange.
    After provisioning, the application key is added and bound via the Proxy
    Service (UUID 0x1828).

    Args:
        flow: Config flow instance.
        user_input: Empty dict when user confirms provisioning (no fields).

    Returns:
        Flow result dict.
    """
    return await _async_step_sig_provision(flow, user_input, DEVICE_TYPE_SIG_PLUG, "sig_plug")


async def async_step_sig_light(flow: Any, user_input: dict[str, Any] | None) -> FlowResult:
    """Handle SIG Mesh light -- auto-provisions, binds lighting models.

    Args:
        flow: Config flow instance.
        user_input: Empty dict when user confirms provisioning (no fields).

    Returns:
        Flow result dict.
    """
    return await _async_step_sig_provision(flow, user_input, DEVICE_TYPE_SIG_LIGHT, "sig_light")


async def _async_step_sig_provision(
    flow: Any, user_input: dict[str, Any] | None, device_type: str, step_id: str
) -> FlowResult:
    """Shared confirm-and-provision step for SIG Mesh plugs and lights."""
    errors: dict[str, str] = {}
    if (
        user_input is not None
        and flow._discovery_info is not None
        and _use_shared_network(flow.hass)
    ):
        from custom_components.tuya_ble_mesh.config_flow_network import (
            async_add_node_to_network,
        )

        mac = flow._discovery_info["address"]
        title = flow._discovery_info.get("name") or mac
        try:
            await async_add_node_to_network(flow.hass, mac, device_type, title)
        except TimeoutError:
            _LOGGER.warning("Provisioning timed out for %s", mac)
            errors["base"] = "timeout"
        except Exception as exc:
            errors["base"] = _classify_provision_error(mac, exc)
        else:
            return flow.async_abort(reason="added_to_network")
    elif user_input is not None and flow._discovery_info is not None:
        mac = flow._discovery_info["address"]
        try:
            node = await run_provision(flow.hass, mac, device_type)
        except TimeoutError:
            _LOGGER.warning("Provisioning timed out for %s", mac)
            errors["base"] = "timeout"
        except NodeConfigurationError:
            errors["base"] = "configuration_failed"
        except Exception as exc:
            errors["base"] = _classify_provision_error(mac, exc)
        else:
            await flow.async_set_unique_id(mac)
            flow._abort_if_unique_id_configured()
            return flow._finalize_entry(mac=mac, device_type=device_type, **node.entry_data())
    return flow.async_show_form(
        step_id=step_id,
        data_schema=vol.Schema({}),
        description_placeholders={
            "name": (flow._discovery_info.get("name", "") if flow._discovery_info else ""),
        },
        errors=errors,
    )


def _use_shared_network(hass: Any) -> bool:
    """New SIG Mesh devices join the shared network (one proxy link, relaying).

    Kept as a function so the legacy one-entry-per-device path stays testable.
    """
    return True


def _classify_provision_error(mac: str, exc: Exception) -> str:
    """Map a provisioning exception to a config flow error key (and log it)."""
    try:
        from tuya_ble_mesh.exceptions import (
            DeviceNotFoundError,
            MeshTimeoutError,
            ProvisioningError,
        )
    except ImportError:
        _LOGGER.warning(
            "Provisioning failed for %s: %s: %s", mac, type(exc).__name__, exc, exc_info=True
        )
        return "provisioning_failed"

    if isinstance(exc, DeviceNotFoundError):
        _LOGGER.warning("Device %s not found during provisioning", mac)
        return "device_not_found"
    if isinstance(exc, MeshTimeoutError):
        _LOGGER.warning("Provisioning timed out (mesh) for %s", mac)
        return "timeout"
    if isinstance(exc, ProvisioningError):
        _LOGGER.warning("Provisioning handshake failed for %s: %s", mac, exc)
        return "provisioning_failed"
    _LOGGER.warning(
        "Provisioning failed for %s: %s: %s", mac, type(exc).__name__, exc, exc_info=True
    )
    return "provisioning_failed"


async def async_step_sig_bridge(flow: Any, user_input: dict[str, Any] | None) -> FlowResult:
    """Handle SIG Mesh Bridge plug configuration.
    Args:
        flow: Config flow instance.
        user_input: User-provided bridge parameters.
    Returns:
        Flow result dict.
    """
    from custom_components.tuya_ble_mesh.config_flow_validators import (
        _test_bridge_with_session,
        _validate_bridge_host,
        _validate_unicast_address,
    )

    errors: dict[str, str] = {}
    if user_input is not None:
        host = user_input.get(CONF_BRIDGE_HOST, "")
        port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
        unicast_target = user_input.get(CONF_UNICAST_TARGET, "00B0")
        host_error = _validate_bridge_host(host)
        if host_error:
            errors[CONF_BRIDGE_HOST] = host_error
        unicast_error = _validate_unicast_address(str(unicast_target))
        if unicast_error:
            errors[CONF_UNICAST_TARGET] = unicast_error
        if not errors:
            if not await _test_bridge_with_session(flow.hass, host, port):
                errors["base"] = "cannot_connect"
            else:
                mac = flow._discovery_info["address"]
                await flow.async_set_unique_id(mac)
                flow._abort_if_unique_id_configured()
                return flow._finalize_entry(
                    mac=mac,
                    device_type=DEVICE_TYPE_SIG_BRIDGE_PLUG,
                    unicast_target=unicast_target,
                    bridge_host=host,
                    bridge_port=port,
                )
    return flow.async_show_form(
        step_id="sig_bridge",
        data_schema=vol.Schema(
            {
                vol.Required(CONF_BRIDGE_HOST): str,
                vol.Optional(CONF_BRIDGE_PORT, default=DEFAULT_BRIDGE_PORT): int,
                vol.Optional(CONF_UNICAST_TARGET, default="00B0"): str,
            }
        ),
        description_placeholders={
            "name": (flow._discovery_info.get("name", "") if flow._discovery_info else ""),
        },
        errors=errors,
    )
