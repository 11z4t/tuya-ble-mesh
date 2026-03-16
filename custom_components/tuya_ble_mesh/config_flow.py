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

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from homeassistant.data_entry_flow import FlowResult

from custom_components.tuya_ble_mesh.const import (
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_DEVICE_TYPE,
    CONF_IV_INDEX,
    CONF_MAC_ADDRESS,
    CONF_MESH_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_UNICAST_TARGET,
    CONF_VENDOR_ID,
    DEFAULT_BRIDGE_PORT,
    DEFAULT_IV_INDEX,
    DEFAULT_MESH_ADDRESS,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
    DOMAIN,
)

# Import handlers from specialized modules
from custom_components.tuya_ble_mesh.config_flow_ble import (
    async_step_bluetooth as ble_bluetooth_handler,
    async_step_confirm_impl,
    validate_and_connect,
)
from custom_components.tuya_ble_mesh.config_flow_sig import (
    async_step_sig_bridge as sig_bridge_handler,
    async_step_sig_plug as sig_plug_handler,
)
from custom_components.tuya_ble_mesh.config_flow_options import (
    async_step_bridge_config as bridge_config_handler,
    async_step_reconfigure as reconfigure_handler,
    async_step_reauth as reauth_handler,
    async_step_reauth_confirm as reauth_confirm_handler,
    async_step_telink_bridge as telink_bridge_handler,
)
from custom_components.tuya_ble_mesh.config_flow_validators import (
    _validate_mac,
    _validate_mesh_credential,
    _validate_vendor_id,
)

from custom_components.tuya_ble_mesh.config_flow_options import TuyaBLEMeshOptionsFlow

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

        _LOGGER.info("Creating config entry: %s (type=%s, data keys=%s)", title, device_type, list(data.keys()))
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

    async def async_step_telink_bridge(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Delegate Telink bridge config to options handler."""
        return await telink_bridge_handler(self, user_input)

    async def async_step_bridge_config(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Delegate bridge config to options handler."""
        return await bridge_config_handler(self, user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Delegate reconfigure to options handler."""
        return await reconfigure_handler(self, user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Delegate reauth to options handler."""
        return await reauth_handler(self, entry_data)

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
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
                    validated_type, extra_data = await self._validate_and_connect(
                        mac.upper(), device_type, mesh_name, mesh_password
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

