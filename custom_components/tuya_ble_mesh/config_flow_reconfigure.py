"""Reconfigure and reauth handlers for config flow."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

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
        flow: ConfigFlow instance.
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
        flow: ConfigFlow instance.
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
        flow: ConfigFlow instance.
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
