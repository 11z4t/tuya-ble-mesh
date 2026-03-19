"""SIG Mesh provisioning for Tuya BLE Mesh config flow.
Handles:
- SIG Mesh plug provisioning via PB-GATT
- SIG Mesh bridge configuration
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

import voluptuous as vol

if TYPE_CHECKING:
    from homeassistant.data_entry_flow import FlowResult
from custom_components.tuya_ble_mesh.const import (
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_UNICAST_TARGET,
    DEFAULT_BRIDGE_PORT,
    DEFAULT_IV_INDEX,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
)

_LOGGER = logging.getLogger(__name__)
# Unicast addresses used during provisioning
_UNICAST_PROVISIONER = 0x0001
_UNICAST_DEVICE_DEFAULT = 0x00B0
# GenericOnOff Server SIG Model ID
_MODEL_GENERIC_ONOFF_SERVER = 0x1000
# Seconds to wait for device to reboot as Proxy Service after provisioning
_POST_PROV_REBOOT_DELAY = 6.0


async def run_provision(hass: Any, mac: str) -> tuple[str, str, str]:
    """Generate keys, provision the device, configure application key and model bind.
    Phase 1: PB-GATT provisioning (Service 0x1827).
    Phase 2: Wait for device to reboot into Proxy Service (0x1828).
    Phase 3: Add application key and bind to GenericOnOff Server model.
    Args:
        hass: Home Assistant instance.
        mac: BLE MAC address of the unprovisioned device.
    Returns:
        Tuple of (net_key_hex, dev_key_hex, app_key_hex).
    Raises:
        ProvisioningError: If PB-GATT provisioning fails.
        Any exception from Phase 3 is logged but not re-raised.
    """
    from bleak import BleakClient
    from bleak_retry_connector import establish_connection
    from homeassistant.components import bluetooth as ha_bluetooth
    from tuya_ble_mesh.secrets import DictSecretsManager  # type: ignore[import-not-found]
    from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice  # type: ignore[import-not-found]
    from tuya_ble_mesh.sig_mesh_provisioner import (
        SIGMeshProvisioner,  # type: ignore[import-not-found]
    )

    # Generate fresh random keys (SECURITY: never logged)
    net_key = os.urandom(16)
    app_key = os.urandom(16)
    _LOGGER.info(
        "Auto-provisioning SIG Mesh device %s (unicast=0x%04X)",
        mac,
        _UNICAST_DEVICE_DEFAULT,
    )

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
        result = await asyncio.wait_for(provisioner.provision(mac), timeout=60.0)
    except TimeoutError:
        raise TimeoutError("Provisioning timed out after 20s") from None
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
    device = SIGMeshDevice(
        mac,
        _UNICAST_DEVICE_DEFAULT,
        _UNICAST_PROVISIONER,
        DictSecretsManager(secrets_dict),
        op_item_prefix=op_prefix,
        iv_index=DEFAULT_IV_INDEX,
    )
    try:
        await device.connect(timeout=20.0, max_retries=5)
        key_add_ok = await device.send_config_app_key_add(app_key)
        if not key_add_ok:
            _LOGGER.warning("Application key add returned non-success for %s", mac)
        await asyncio.sleep(0.5)
        bind_ok = await device.send_config_model_app_bind(
            _UNICAST_DEVICE_DEFAULT, 0, _MODEL_GENERIC_ONOFF_SERVER
        )
        if not bind_ok:
            _LOGGER.warning(
                "Model App Bind returned non-success for %s (model=0x%04X)",
                mac,
                _MODEL_GENERIC_ONOFF_SERVER,
            )
    except Exception:
        _LOGGER.warning(
            "Post-provisioning config failed for %s",
            mac,
            exc_info=True,
        )
    finally:
        await device.disconnect()
    return net_key.hex(), result.dev_key.hex(), app_key.hex()


async def async_step_sig_plug(flow: Any, user_input: dict[str, Any] | None) -> FlowResult:
    """Handle SIG Mesh plug -- auto-provisions and generates all keys.
    The device is provisioned via PB-GATT (Service UUID 0x1827).
    A random network key and device key are established via a secure key exchange.
    After provisioning, the application key is added and bound to the
    GenericOnOff Server model via the Proxy Service (UUID 0x1828).
    Args:
        flow: Config flow instance.
        user_input: Empty dict when user confirms provisioning (no fields).
    Returns:
        Flow result dict.
    """
    errors: dict[str, str] = {}
    if user_input is not None and flow._discovery_info is not None:
        mac = flow._discovery_info["address"]
        try:
            net_key_hex, dev_key_hex, app_key_hex = await run_provision(flow.hass, mac)
        except TimeoutError:
            _LOGGER.warning("Provisioning timed out for %s", mac)
            errors["base"] = "timeout"
        except Exception as exc:
            # Import here to avoid circular dep at module level
            _error_key = "provisioning_failed"
            try:
                from tuya_ble_mesh.exceptions import (  # type: ignore[import-not-found]
                    DeviceNotFoundError,
                    ProvisioningError,
                )
                from tuya_ble_mesh.exceptions import (
                    TimeoutError as MeshTimeoutError,
                )

                if isinstance(exc, DeviceNotFoundError):
                    _LOGGER.warning("Device %s not found during provisioning", mac)
                    _error_key = "device_not_found"
                elif isinstance(exc, MeshTimeoutError):
                    _LOGGER.warning("Provisioning timed out (mesh) for %s", mac)
                    _error_key = "timeout"
                elif isinstance(exc, ProvisioningError):
                    _LOGGER.warning("Provisioning handshake failed for %s: %s", mac, exc)
                    _error_key = "provisioning_failed"
                else:
                    _LOGGER.warning(
                        "Provisioning failed for %s: %s: %s",
                        mac,
                        type(exc).__name__,
                        exc,
                        exc_info=True,
                    )
            except ImportError:
                _LOGGER.warning(
                    "Provisioning failed for %s: %s: %s",
                    mac,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
            errors["base"] = _error_key
        else:
            await flow.async_set_unique_id(mac)
            flow._abort_if_unique_id_configured()
            return flow._finalize_entry(
                mac=mac,
                device_type=DEVICE_TYPE_SIG_PLUG,
                unicast_target=f"{_UNICAST_DEVICE_DEFAULT:04X}",
                unicast_our=f"{_UNICAST_PROVISIONER:04X}",
                iv_index=DEFAULT_IV_INDEX,
                net_key=net_key_hex,
                dev_key=dev_key_hex,
                app_key=app_key_hex,
            )
    return flow.async_show_form(
        step_id="sig_plug",
        data_schema=vol.Schema({}),
        description_placeholders={
            "name": (flow._discovery_info.get("name", "") if flow._discovery_info else ""),
        },
        errors=errors,
    )


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
