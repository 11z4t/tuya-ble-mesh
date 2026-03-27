"""Config flow for Tuya BLE Mesh integration.
This module routes config flow steps to specialized handlers:
- config_flow_ble: BLE discovery, validation, confirmation
- config_flow_sig: SIG Mesh provisioning
- config_flow_options: Bridge + reconfigure + reauth
- config_flow_validators: Validation helpers
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow

# Import for test patching (used in config_flow_sig/config_flow_telink submodules)
try:
    from bleak import BleakScanner
    from tuya_ble_mesh.sig_mesh_bridge import SIGMeshBridgeDevice
    from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice

    find_device_by_address = BleakScanner.find_device_by_address
except ImportError:
    SIGMeshBridgeDevice = None  # type: ignore[misc,assignment]
    SIGMeshDevice = None  # type: ignore[misc,assignment]
    find_device_by_address = None  # type: ignore[misc,assignment]

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from homeassistant.data_entry_flow import FlowResult

# Import handlers from specialized modules
from custom_components.tuya_ble_mesh.config_flow_ble import validate_and_connect
from custom_components.tuya_ble_mesh.config_flow_discovery import (
    async_step_bluetooth as ble_bluetooth_handler,
)
from custom_components.tuya_ble_mesh.config_flow_discovery import (
    async_step_confirm_impl,
)
from custom_components.tuya_ble_mesh.config_flow_options import TuyaBLEMeshOptionsFlow
from custom_components.tuya_ble_mesh.config_flow_options import (
    async_step_bridge_config as bridge_config_handler,
)
from custom_components.tuya_ble_mesh.config_flow_reconfigure import (
    async_step_reauth as reauth_handler,
)
from custom_components.tuya_ble_mesh.config_flow_reconfigure import (
    async_step_reauth_confirm as reauth_confirm_handler,
)
from custom_components.tuya_ble_mesh.config_flow_reconfigure import (
    async_step_reconfigure as reconfigure_handler,
)
from custom_components.tuya_ble_mesh.config_flow_sig import (
    async_step_sig_bridge as sig_bridge_handler,
)
from custom_components.tuya_ble_mesh.config_flow_sig import (
    async_step_sig_plug as sig_plug_handler,
)
from custom_components.tuya_ble_mesh.config_flow_telink import (
    async_step_telink_bridge as telink_bridge_handler,
)
from custom_components.tuya_ble_mesh.config_flow_validators import (
    _validate_mac,
    _validate_mesh_credential,
    _validate_vendor_id,
)
from custom_components.tuya_ble_mesh.const import (
    CONF_APP_KEY,
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_DEV_KEY,
    CONF_DEVICE_TYPE,
    CONF_IV_INDEX,
    CONF_MAC_ADDRESS,
    CONF_MESH_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_NET_KEY,
    CONF_UNICAST_OUR,
    CONF_UNICAST_TARGET,
    CONF_VENDOR_ID,
    DEFAULT_MESH_ADDRESS,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class TuyaBLEMeshConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle a config flow for Tuya BLE Mesh."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TuyaBLEMeshOptionsFlow:
        """Return the options flow handler."""
        return TuyaBLEMeshOptionsFlow(config_entry)

    def __init__(self) -> None:
        """Initialize config flow state for a new Tuya BLE Mesh entry."""
        super().__init__()
        self._discovery_info: dict[str, Any] | None = None

    def _finalize_entry(
        self,
        mac: str,
        device_type: str,
        title: str | None = None,
        **extra_data: Any,
    ) -> FlowResult:
        """Create config entry after all validation passed.

        PLAT-740 QC BRIST 1: SINGLE entry point for async_create_entry.
        ALL code paths must call this method to create entries.

        Args:
            mac: Device MAC address.
            device_type: Validated device type.
            title: Entry title (auto-generated if None).
            **extra_data: Additional config data (mesh_name, keys, etc.).
                Accepts both snake_case kwargs and CONF_* constants.

        Returns:
            FlowResult from async_create_entry.
        """
        short_mac = mac[-8:]
        if title is None:
            type_label = {
                DEVICE_TYPE_LIGHT: "LED Light",
                DEVICE_TYPE_PLUG: "Smart Plug",
                DEVICE_TYPE_SIG_PLUG: "Smart Plug",
                DEVICE_TYPE_SIG_BRIDGE_PLUG: "Smart Plug",
                DEVICE_TYPE_TELINK_BRIDGE_LIGHT: "LED Light",
            }.get(device_type, "Smart Device")
            title = f"{type_label} {short_mac}"

        # Map snake_case kwargs to CONF_* constants
        key_map = {
            "mesh_name": CONF_MESH_NAME,
            "mesh_password": CONF_MESH_PASSWORD,
            "vendor_id": CONF_VENDOR_ID,
            "mesh_address": CONF_MESH_ADDRESS,
            "unicast_target": CONF_UNICAST_TARGET,
            "unicast_our": CONF_UNICAST_OUR,
            "iv_index": CONF_IV_INDEX,
            "net_key": CONF_NET_KEY,
            "dev_key": CONF_DEV_KEY,
            "app_key": CONF_APP_KEY,
            "bridge_host": CONF_BRIDGE_HOST,
            "bridge_port": CONF_BRIDGE_PORT,
        }
        data = {CONF_MAC_ADDRESS: mac, CONF_DEVICE_TYPE: device_type}
        for key, value in extra_data.items():
            conf_key = key_map.get(key, key)
            data[conf_key] = value

        _LOGGER.info(
            "Creating config entry: %s (type=%s, data keys=%s)",
            title,
            device_type,
            list(data.keys()),
        )
        return self.async_create_entry(title=title, data=data)

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak) -> FlowResult:
        """Delegate bluetooth discovery to BLE handler."""
        return await ble_bluetooth_handler(self, discovery_info)

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Delegate discovery confirmation to BLE handler."""
        return await async_step_confirm_impl(self, user_input)

    async def async_step_sig_plug(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Delegate SIG Mesh plug provisioning to SIG handler."""
        return await sig_plug_handler(self, user_input)

    async def async_step_sig_bridge(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Delegate SIG bridge config to SIG handler."""
        return await sig_bridge_handler(self, user_input)

    async def async_step_telink_bridge(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Delegate Telink bridge config to options handler."""
        return await telink_bridge_handler(self, user_input)

    async def async_step_bridge_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Delegate bridge config to options handler."""
        return await bridge_config_handler(self, user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Delegate reconfigure to options handler."""
        return await reconfigure_handler(self, user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Delegate reauth to options handler."""
        return await reauth_handler(self, entry_data)

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Delegate reauth_confirm to options handler."""
        return await reauth_confirm_handler(self, user_input)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:  # type: ignore[override]
        """Handle manual setup.

        Args:
            user_input: User-provided configuration data.

        Returns:
            Flow result dict.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            mac = user_input.get(CONF_MAC_ADDRESS, "")
            mac_error = _validate_mac(mac)
            if mac_error:
                errors[CONF_MAC_ADDRESS] = mac_error

            mesh_name = user_input.get(CONF_MESH_NAME, "")
            name_error = _validate_mesh_credential(mesh_name)
            if name_error:
                errors[CONF_MESH_NAME] = name_error

            mesh_password = user_input.get(CONF_MESH_PASSWORD, "")
            pass_error = _validate_mesh_credential(mesh_password)
            if pass_error:
                errors[CONF_MESH_PASSWORD] = pass_error

            vendor_id_str = user_input.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID)
            vendor_id_error = _validate_vendor_id(str(vendor_id_str))
            if vendor_id_error:
                errors[CONF_VENDOR_ID] = vendor_id_error

            if not errors:
                # Check for duplicate MAC address
                try:
                    for _entry in self.hass.config_entries.async_entries(DOMAIN):
                        if _entry.data.get(CONF_MAC_ADDRESS, "").upper() == mac.upper():
                            return self.async_abort(reason="already_configured")
                except Exception:
                    # Config entries API failure - proceed with setup
                    _LOGGER.debug("Failed to check for duplicate MAC", exc_info=True)

                device_type = user_input.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)

                # Bridge devices: skip validation (they don't use BLE)
                if device_type == DEVICE_TYPE_SIG_BRIDGE_PLUG:
                    self._discovery_info = {
                        "address": mac.upper(),
                        "name": f"Smart Plug {mac[-8:]}",
                    }
                    return await self.async_step_sig_bridge(None)
                if device_type == DEVICE_TYPE_TELINK_BRIDGE_LIGHT:
                    self._discovery_info = {
                        "address": mac.upper(),
                        "name": f"LED Light {mac[-8:]}",
                    }
                    return await self.async_step_telink_bridge(None)

                # SIG Mesh plug: use existing provisioning flow
                if device_type == DEVICE_TYPE_SIG_PLUG:
                    self._discovery_info = {
                        "address": mac.upper(),
                        "name": f"Smart Plug {mac[-8:]}",
                    }
                    return await self.async_step_sig_plug(None)

                # PLAT-740: Direct BLE devices — validate before creating entry
                mesh_name = user_input.get(CONF_MESH_NAME, "out_of_mesh")
                mesh_password = user_input.get(CONF_MESH_PASSWORD, "123456")

                try:
                    validated_type, _extra_data = await validate_and_connect(
                        self.hass, mac.upper(), device_type, mesh_name, mesh_password
                    )
                    device_type = validated_type
                except ValueError as exc:
                    error_key = str(exc).strip("'\"")
                    errors["base"] = error_key
                except Exception as exc:
                    _LOGGER.warning("Validation failed for %s: %s", mac, exc, exc_info=True)
                    errors["base"] = "cannot_connect_ble"

            if not errors:
                await self.async_set_unique_id(mac.upper())
                self._abort_if_unique_id_configured()
                return self._finalize_entry(
                    mac=mac.upper(),
                    device_type=device_type,
                    mesh_name=user_input.get(CONF_MESH_NAME, "out_of_mesh"),
                    mesh_password=user_input.get(CONF_MESH_PASSWORD, "123456"),
                    vendor_id=user_input.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID),
                    mesh_address=user_input.get(CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS),
                )

        # UX-1.4: 3 user-facing device types (SIG types auto-detected via Bluetooth discovery)
        # UX-1.5: Progressive disclosure -- advanced fields shown only in HA advanced mode
        schema_dict: dict[object, object] = {
            vol.Required(CONF_MAC_ADDRESS): str,
            vol.Required(CONF_DEVICE_TYPE, default=DEVICE_TYPE_LIGHT): vol.In(
                {
                    DEVICE_TYPE_LIGHT: "LED Light",
                    DEVICE_TYPE_PLUG: "Smart Plug",
                    DEVICE_TYPE_SIG_PLUG: "Smart Plug (SIG Mesh)",
                    DEVICE_TYPE_TELINK_BRIDGE_LIGHT: "LED Light (via bridge)",
                }
            ),
        }
        if self.show_advanced_options:
            schema_dict[vol.Optional(CONF_MESH_NAME, default="out_of_mesh")] = str
            schema_dict[vol.Optional(CONF_MESH_PASSWORD, default="123456")] = str
            schema_dict[vol.Optional(CONF_VENDOR_ID, default=DEFAULT_VENDOR_ID)] = str
            schema_dict[vol.Optional(CONF_MESH_ADDRESS, default=DEFAULT_MESH_ADDRESS)] = int

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={},
            errors=errors,
        )

    async def async_step_sig_plug(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle SIG Mesh plug — auto-provisions and generates all keys.

        The device is provisioned via PB-GATT (Service UUID 0x1827).
        A random network key and device key are established via a secure key exchange.
        After provisioning, the application key is added and bound to the
        GenericOnOff Server model via the Proxy Service (UUID 0x1828).

        Args:
            user_input: Empty dict when user confirms provisioning (no fields).

        Returns:
            Flow result dict.
        """
        errors: dict[str, str] = {}

        if user_input is not None and self._discovery_info is not None:
            mac = self._discovery_info["address"]
            try:
                net_key_hex, dev_key_hex, app_key_hex = await self._run_provision(mac)
            except asyncio.TimeoutError:
                _LOGGER.warning("Provisioning timed out for %s", mac)
                errors["base"] = "timeout"
            except Exception as exc:
                # Import here to avoid circular dep at module level
                _error_key = "provisioning_failed"
                try:
                    from tuya_ble_mesh.exceptions import (  # type: ignore[import-not-found]
                        DeviceNotFoundError,
                        ProvisioningError,
                        TimeoutError as MeshTimeoutError,
                    )

                    if isinstance(exc, DeviceNotFoundError):
                        _LOGGER.warning("Device %s not found during provisioning", mac)
                        _error_key = "device_not_found"
                    elif isinstance(exc, MeshTimeoutError):
                        _LOGGER.warning("Provisioning timed out (mesh) for %s", mac)
                        _error_key = "timeout"
                    elif isinstance(exc, ProvisioningError):
                        _exc_str = str(exc).lower()
                        if "appkey" in _exc_str:
                            _error_key = "provisioning_appkey_failed"
                        elif "proxy" in _exc_str:
                            _error_key = "provisioning_proxy_failed"
                        elif "pbgatt" in _exc_str or "pb-gatt" in _exc_str:
                            _error_key = "provisioning_pbgatt_failed"
                        else:
                            _error_key = "provisioning_failed"
                        _LOGGER.warning(
                            "Provisioning handshake failed for %s: %s", mac, exc
                        )
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
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Smart Plug {mac[-8:]}",
                    data={
                        CONF_MAC_ADDRESS: mac,
                        CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_PLUG,
                        CONF_UNICAST_TARGET: f"{_UNICAST_DEVICE_DEFAULT:04X}",
                        CONF_UNICAST_OUR: f"{_UNICAST_PROVISIONER:04X}",
                        CONF_IV_INDEX: DEFAULT_IV_INDEX,
                        CONF_NET_KEY: net_key_hex,
                        CONF_DEV_KEY: dev_key_hex,
                        CONF_APP_KEY: app_key_hex,
                    },
                )

        return self.async_show_form(
            step_id="sig_plug",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": (self._discovery_info.get("name", "") if self._discovery_info else ""),
            },
            errors=errors,
        )

    async def _run_provision(self, mac: str) -> tuple[str, str, str]:
        """Generate keys, provision the device, configure application key and model bind.

        Phase 1: PB-GATT provisioning (Service 0x1827).
        Phase 2: Wait for device to reboot into Proxy Service (0x1828).
        Phase 3: Add application key and bind to GenericOnOff Server model.

        Args:
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
        from tuya_ble_mesh.sig_mesh_provisioner import SIGMeshProvisioner  # type: ignore[import-not-found]

        # Generate fresh random keys (SECURITY: never logged)
        net_key = os.urandom(16)
        app_key = os.urandom(16)

        _LOGGER.info(
            "Auto-provisioning SIG Mesh device %s (unicast=0x%04X)",
            mac,
            _UNICAST_DEVICE_DEFAULT,
        )

        # HA Bluetooth callbacks — use retry-connector to avoid HA warning
        # NOTE: Works with ESPHome BLE proxies. If HA has no local adapter but has
        # ESPHome BLE proxies, devices discovered by proxies will be in HA's bluetooth
        # registry and establish_connection will route traffic via the proxy.
        def _ble_device_cb(address: str) -> Any:
            """Look up BLEDevice via HA bluetooth registry (non-connectable OK)."""
            device = ha_bluetooth.async_ble_device_from_address(
                self.hass, address.upper(), connectable=True
            )
            if device is None:
                _LOGGER.debug(
                    "No connectable BLEDevice for %s, trying non-connectable", address
                )
                device = ha_bluetooth.async_ble_device_from_address(
                    self.hass, address.upper(), connectable=False
                )
            if device is None:
                _LOGGER.warning(
                    "BLEDevice not found in HA bluetooth registry for %s. "
                    "Ensure device is in range of a BLE adapter or ESPHome BLE proxy.",
                    address,
                )
            else:
                _LOGGER.debug("Found BLEDevice for %s: %s", address, device)
            return device

        async def _ble_connect_cb(ble_device: Any) -> BleakClient:
            """Connect via bleak-retry-connector to avoid HA BleakClient warning."""
            return await establish_connection(
                BleakClient,
                ble_device,
                ble_device.address,
                max_attempts=5,
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
        result = await provisioner.provision(mac)

        _LOGGER.info(
            "PB-GATT provisioning succeeded for %s (%d elements)",
            mac,
            result.num_elements,
        )

        # Phase 2: Wait for device to reboot and switch to Proxy Service
        _LOGGER.info(
            "Waiting %.0fs for %s to reboot as Proxy Service...", _POST_PROV_REBOOT_DELAY, mac
        )
        await asyncio.sleep(_POST_PROV_REBOOT_DELAY)

        # Phase 3: Post-provisioning config via GATT Proxy
        op_prefix = "cfg"
        target_hex = f"{_UNICAST_DEVICE_DEFAULT:04x}"
        dev_key_name = f"{op_prefix}-dev-key-{target_hex}/password"
        secrets_dict = {
            f"{op_prefix}-net-key/password": net_key.hex(),  # pragma: allowlist secret
            dev_key_name: result.dev_key.hex(),  # pragma: allowlist secret
            f"{op_prefix}-app-key/password": app_key.hex(),  # pragma: allowlist secret
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
                "Post-provisioning config failed for %s (device provisioned but application key not bound)",
                mac,
                exc_info=True,
            )
        finally:
            await device.disconnect()

        return net_key.hex(), result.dev_key.hex(), app_key.hex()

    async def async_step_sig_bridge(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle SIG Mesh Bridge plug configuration.

        Args:
            user_input: User-provided bridge parameters.

        Returns:
            Flow result dict.
        """
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
                if not await _test_bridge_with_session(self.hass, host, port):
                    errors["base"] = "cannot_connect"
                else:
                    mac = self._discovery_info["address"]
                    await self.async_set_unique_id(mac)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Smart Plug {mac[-8:]}",
                        data={
                            CONF_MAC_ADDRESS: mac,
                            CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_BRIDGE_PLUG,
                            CONF_UNICAST_TARGET: unicast_target,
                            CONF_BRIDGE_HOST: host,
                            CONF_BRIDGE_PORT: port,
                        },
                    )

        return self.async_show_form(
            step_id="sig_bridge",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_HOST): str,
                    vol.Optional(CONF_BRIDGE_PORT, default=DEFAULT_BRIDGE_PORT): int,
                    vol.Optional(CONF_UNICAST_TARGET, default="00B0"): str,
                }
            ),
            description_placeholders={
                "name": (self._discovery_info.get("name", "") if self._discovery_info else ""),
                "status": "Testing connection..." if user_input is not None else "",
            },
            errors=errors,
        )

    async def async_step_telink_bridge(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle Telink Bridge light configuration.

        Args:
            user_input: User-provided bridge parameters.

        Returns:
            Flow result dict.
        """
        errors: dict[str, str] = {}
        if user_input is not None and self._discovery_info is not None:
            host = user_input.get(CONF_BRIDGE_HOST, "")
            port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
            host_error = _validate_bridge_host(host)
            if host_error:
                errors[CONF_BRIDGE_HOST] = host_error
            elif not await _test_bridge_with_session(self.hass, host, port):
                errors["base"] = "cannot_connect"
            else:
                mac = self._discovery_info["address"]
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"LED Light {mac[-8:]}",
                    data={
                        CONF_MAC_ADDRESS: mac,
                        CONF_DEVICE_TYPE: DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
                        CONF_BRIDGE_HOST: host,
                        CONF_BRIDGE_PORT: port,
                    },
                )

        return self.async_show_form(
            step_id="telink_bridge",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_HOST): str,
                    vol.Optional(CONF_BRIDGE_PORT, default=DEFAULT_BRIDGE_PORT): int,
                }
            ),
            description_placeholders={
                "name": (self._discovery_info.get("name", "") if self._discovery_info else ""),
                "status": "Testing connection..." if user_input is not None else "",
            },
            errors=errors,
        )

    async def async_step_bridge_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle generic bridge configuration (alias used by tests).

        Args:
            user_input: User-provided bridge parameters.

        Returns:
            Flow result dict.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input.get(CONF_BRIDGE_HOST, "")
            port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
            host_error = _validate_bridge_host(host)
            if host_error:
                errors[CONF_BRIDGE_HOST] = host_error

        return self.async_show_form(
            step_id="bridge_config",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_HOST): str,
                    vol.Optional(CONF_BRIDGE_PORT, default=DEFAULT_BRIDGE_PORT): int,
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration of an existing entry.

        Called from the HA device page → 'Reconfigure' menu item.
        Allows updating connection settings (host, port, mesh credentials)
        without removing and re-adding the device.

        Device-type-aware: bridge devices show host/port (with live connectivity
        test), direct BLE devices show mesh credentials, SIG Mesh plugs allow
        updating unicast address and IV index.

        Args:
            user_input: Updated connection settings from the user.

        Returns:
            Flow result dict.
        """
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            return self.async_abort(reason="entry_not_found")

        device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)
        errors: dict[str, str] = {}

        if user_input is not None:
            if device_type in (DEVICE_TYPE_SIG_BRIDGE_PLUG, DEVICE_TYPE_TELINK_BRIDGE_LIGHT):
                host = user_input.get(CONF_BRIDGE_HOST, "")
                port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
                host_error = _validate_bridge_host(host)
                if host_error:
                    errors[CONF_BRIDGE_HOST] = host_error
                elif not await _test_bridge_with_session(self.hass, host, port):
                    errors["base"] = "cannot_connect"
            elif device_type == DEVICE_TYPE_SIG_PLUG:
                unicast_val = str(user_input.get(CONF_UNICAST_TARGET, "00B0"))
                unicast_error = _validate_unicast_address(unicast_val)
                if unicast_error:
                    errors[CONF_UNICAST_TARGET] = unicast_error
                iv_val = user_input.get(CONF_IV_INDEX, 0)
                iv_error = _validate_iv_index(iv_val)
                if iv_error:
                    errors[CONF_IV_INDEX] = iv_error
            else:
                name_val = user_input.get(CONF_MESH_NAME, "")
                if name_val:
                    name_error = _validate_mesh_credential(str(name_val))
                    if name_error:
                        errors[CONF_MESH_NAME] = name_error
                pass_val = user_input.get(CONF_MESH_PASSWORD, "")
                if pass_val:
                    pass_error = _validate_mesh_credential(str(pass_val))
                    if pass_error:
                        errors[CONF_MESH_PASSWORD] = pass_error

            if not errors:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **user_input}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")

        # Build schema based on device type
        if device_type in (DEVICE_TYPE_SIG_BRIDGE_PLUG, DEVICE_TYPE_TELINK_BRIDGE_LIGHT):
            schema = vol.Schema(
                {
                    vol.Required(
                        CONF_BRIDGE_HOST,
                        default=entry.data.get(CONF_BRIDGE_HOST, ""),
                    ): str,
                    vol.Optional(
                        CONF_BRIDGE_PORT,
                        default=entry.data.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT),
                    ): int,
                }
            )
        elif device_type == DEVICE_TYPE_SIG_PLUG:
            schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_UNICAST_TARGET,
                        default=entry.data.get(CONF_UNICAST_TARGET, "00B0"),
                    ): str,
                    vol.Optional(
                        CONF_IV_INDEX,
                        default=entry.data.get(CONF_IV_INDEX, DEFAULT_IV_INDEX),
                    ): int,
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_MESH_NAME,
                        default=entry.data.get(CONF_MESH_NAME, "out_of_mesh"),
                    ): str,
                    vol.Optional(
                        CONF_MESH_PASSWORD,
                        default=entry.data.get(CONF_MESH_PASSWORD, ""),
                    ): str,
                }
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            description_placeholders={"name": entry.title},
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauth when mesh credentials fail.

        Triggered by the coordinator when auth errors occur (e.g. wrong mesh
        password after credentials are rotated on the device).

        Args:
            entry_data: Existing config entry data (unused — shown for context).

        Returns:
            Flow result dict.
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-enter mesh credentials after authentication failure.

        Args:
            user_input: New credentials from the user.

        Returns:
            Flow result dict.
        """
        errors: dict[str, str] = {}

        entry = self.hass.config_entries.async_get_entry(self.context.get("entry_id", ""))

        if user_input is not None and entry is not None:
            new_data = {**entry.data, **user_input}
            self.hass.config_entries.async_update_entry(entry, data=new_data)
            await self.hass.config_entries.async_reload(entry.entry_id)
            return self.async_abort(reason="reauth_successful")

        device_type = (
            entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT) if entry else DEVICE_TYPE_LIGHT
        )
        if device_type in (DEVICE_TYPE_SIG_BRIDGE_PLUG, DEVICE_TYPE_TELINK_BRIDGE_LIGHT):
            schema = vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_HOST): str,
                    vol.Optional(CONF_BRIDGE_PORT, default=DEFAULT_BRIDGE_PORT): int,
                }
            )
        else:
            schema = vol.Schema(
                {
                    vol.Optional(CONF_MESH_NAME, default="out_of_mesh"): str,
                    vol.Optional(CONF_MESH_PASSWORD, default=""): str,
                }
            )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )
