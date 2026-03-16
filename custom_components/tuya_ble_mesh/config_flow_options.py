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




async def async_step_telink_bridge(
    flow: Any, user_input: dict[str, Any] | None = None
) -> FlowResult:
        """Handle Telink Bridge light configuration.

        Args:
            user_input: User-provided bridge parameters.

        Returns:
            Flow result dict.
        """
        errors: dict[str, str] = {}
        if user_input is not None and flow._discovery_info is not None:
            host = user_input.get(CONF_BRIDGE_HOST, "")
            port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
            host_error = _validate_bridge_host(host)
            if host_error:
                errors[CONF_BRIDGE_HOST] = host_error
            elif not await _test_bridge_with_session(flow.hass, host, port):
                errors["base"] = "cannot_connect"
            else:
                mac = flow._discovery_info["address"]
                await flow.async_set_unique_id(mac)
                flow._abort_if_unique_id_configured()
                return flow._finalize_entry(
                    mac=mac,
                    device_type=DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
                    bridge_host=host,
                    bridge_port=port,
                )

        return flow.async_show_form(
            step_id="telink_bridge",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_HOST): str,
                    vol.Optional(CONF_BRIDGE_PORT, default=DEFAULT_BRIDGE_PORT): int,
                }
            ),
            description_placeholders={
                "name": (flow._discovery_info.get("name", "") if flow._discovery_info else ""),
            },
            errors=errors,
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

async def async_step_reconfigure(
        flow, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle reconfiguration of an existing entry.

        Called from the HA device page -> 'Reconfigure' menu item.
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
        entry = flow.hass.config_entries.async_get_entry(flow.context["entry_id"])
        if entry is None:
            return flow.async_abort(reason="entry_not_found")

        device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)
        errors: dict[str, str] = {}

        if user_input is not None:
            if device_type in (DEVICE_TYPE_SIG_BRIDGE_PLUG, DEVICE_TYPE_TELINK_BRIDGE_LIGHT):
                host = user_input.get(CONF_BRIDGE_HOST, "")
                port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
                host_error = _validate_bridge_host(host)
                if host_error:
                    errors[CONF_BRIDGE_HOST] = host_error
                elif not await _test_bridge_with_session(flow.hass, host, port):
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
                flow.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **user_input}
                )
                await flow.hass.config_entries.async_reload(entry.entry_id)
                return flow.async_abort(reason="reconfigure_successful")

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

        return flow.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            description_placeholders={"name": entry.title},
            errors=errors,
        )

async def async_step_reauth(flow, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauth when mesh credentials fail.

        Triggered by the coordinator when auth errors occur (e.g. wrong mesh
        password after credentials are rotated on the device).

        Args:
            entry_data: Existing config entry data (unused -- shown for context).

        Returns:
            Flow result dict.
        """
        return await flow.async_step_reauth_confirm()

async def async_step_reauth_confirm(
        flow, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-enter mesh credentials after authentication failure.

        Args:
            user_input: New credentials from the user.

        Returns:
            Flow result dict.
        """
        errors: dict[str, str] = {}

        entry = flow.hass.config_entries.async_get_entry(flow.context.get("entry_id", ""))

        if user_input is not None and entry is not None:
            new_data = {**entry.data, **user_input}
            flow.hass.config_entries.async_update_entry(entry, data=new_data)
            await flow.hass.config_entries.async_reload(entry.entry_id)
            return flow.async_abort(reason="reauth_successful")

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

        return flow.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )
