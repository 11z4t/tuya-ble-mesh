"""Bridge configuration and reconfigure/reauth handlers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries

if TYPE_CHECKING:
    from homeassistant.data_entry_flow import FlowResult

from custom_components.tuya_ble_mesh.const import (
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_DEVICE_TYPE,
    CONF_IV_INDEX,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_UNICAST_TARGET,
    DEFAULT_BRIDGE_PORT,
    DEFAULT_IV_INDEX,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
)
from custom_components.tuya_ble_mesh.config_flow_validators import (
    _test_bridge_with_session,
    _validate_bridge_host,
    _validate_iv_index,
    _validate_mesh_credential,
    _validate_unicast_address,
)

_LOGGER = logging.getLogger(__name__)

class TuyaBLEMeshOptionsFlow(config_entries.OptionsFlow):
    """Handle options for a Tuya BLE Mesh entry."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow with the existing config entry.

        Args:
            config_entry: The config entry whose options are being edited.
        """
        super().__init__()
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage device options.

        Args:
            user_input: User-provided option values.

        Returns:
            Flow result dict.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            device_type = self._config_entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)

            # Validate SIG plug-specific options
            if device_type == DEVICE_TYPE_SIG_PLUG:
                unicast_val = str(user_input.get(CONF_UNICAST_TARGET, "00B0"))
                unicast_error = _validate_unicast_address(unicast_val)
                if unicast_error:
                    errors[CONF_UNICAST_TARGET] = unicast_error
                iv_val = user_input.get(CONF_IV_INDEX, 0)
                iv_error = _validate_iv_index(iv_val)
                if iv_error:
                    errors[CONF_IV_INDEX] = iv_error

            # Validate bridge host for bridge devices
            if device_type in (DEVICE_TYPE_SIG_BRIDGE_PLUG, DEVICE_TYPE_TELINK_BRIDGE_LIGHT):
                host_val = user_input.get(CONF_BRIDGE_HOST, "")
                if host_val:
                    host_error = _validate_bridge_host(str(host_val))
                    if host_error:
                        errors[CONF_BRIDGE_HOST] = host_error

            # Validate mesh credentials for direct BLE devices
            if device_type in (DEVICE_TYPE_LIGHT, DEVICE_TYPE_PLUG):
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
                # Merge new data into config entry
                new_data = {**self._config_entry.data, **user_input}
                self.hass.config_entries.async_update_entry(self._config_entry, data=new_data)
                return self.async_create_entry(title="", data={})

        device_type = self._config_entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)

        # UX-1.7: Build schema based on device type with progressive disclosure.
        # Normal view: credentials/connection settings that users may legitimately change.
        # Advanced mode: low-level mesh addressing fields (unicast, iv_index, mesh_address).
        schema_dict: dict[object, object] = {}

        if device_type in (DEVICE_TYPE_SIG_BRIDGE_PLUG, DEVICE_TYPE_TELINK_BRIDGE_LIGHT):
            # Bridge devices: show host/port always; unicast is advanced-only
            schema_dict[
                vol.Optional(
                    CONF_BRIDGE_HOST,
                    default=self._config_entry.data.get(CONF_BRIDGE_HOST, ""),
                )
            ] = str
            schema_dict[
                vol.Optional(
                    CONF_BRIDGE_PORT,
                    default=self._config_entry.data.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT),
                )
            ] = int
            if self.show_advanced_options and device_type == DEVICE_TYPE_SIG_BRIDGE_PLUG:
                schema_dict[
                    vol.Optional(
                        CONF_UNICAST_TARGET,
                        default=self._config_entry.data.get(CONF_UNICAST_TARGET, "00B0"),
                    )
                ] = str
        elif device_type == DEVICE_TYPE_SIG_PLUG:
            # SIG Mesh plug: unicast and iv_index are advanced network settings
            if self.show_advanced_options:
                schema_dict[
                    vol.Optional(
                        CONF_UNICAST_TARGET,
                        default=self._config_entry.data.get(CONF_UNICAST_TARGET, "00B0"),
                    )
                ] = str
                schema_dict[
                    vol.Optional(
                        CONF_IV_INDEX,
                        default=self._config_entry.data.get(CONF_IV_INDEX, DEFAULT_IV_INDEX),
                    )
                ] = int
        else:
            # Direct BLE devices: mesh credentials always visible; mesh_address is advanced
            schema_dict[
                vol.Optional(
                    CONF_MESH_NAME,
                    default=self._config_entry.data.get(CONF_MESH_NAME, "out_of_mesh"),
                )
            ] = str
            schema_dict[
                vol.Optional(
                    CONF_MESH_PASSWORD,
                    default=self._config_entry.data.get(
                        CONF_MESH_PASSWORD,
                        "123456",
                    ),
                )
            ] = str
            if self.show_advanced_options:
                schema_dict[
                    vol.Optional(
                        CONF_MESH_ADDRESS,
                        default=self._config_entry.data.get(
                            CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS
                        ),
                    )
                ] = int

        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(schema_dict), errors=errors
        )


async def async_step_bridge_config(
        flow, user_input: dict[str, Any] | None = None
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
            user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
            host_error = _validate_bridge_host(host)
            if host_error:
                errors[CONF_BRIDGE_HOST] = host_error

        return flow.async_show_form(
            step_id="bridge_config",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_HOST): str,
                    vol.Optional(CONF_BRIDGE_PORT, default=DEFAULT_BRIDGE_PORT): int,
                }
            ),
            errors=errors,
        )
