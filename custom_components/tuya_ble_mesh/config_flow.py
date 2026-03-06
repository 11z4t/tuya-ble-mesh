"""Config flow for Tuya BLE Mesh integration.

Supports bluetooth discovery (out_of_mesh*, tymesh*) and manual MAC entry.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigFlow

from custom_components.tuya_ble_mesh.const import (
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_DEVICE_TYPE,
    CONF_IV_INDEX,
    CONF_MAC_ADDRESS,
    CONF_MESH_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_OP_ITEM_PREFIX,
    CONF_UNICAST_OUR,
    CONF_UNICAST_TARGET,
    CONF_VENDOR_ID,
    DEFAULT_BRIDGE_PORT,
    DEFAULT_IV_INDEX,
    DEFAULT_MESH_ADDRESS,
    DEFAULT_OP_ITEM_PREFIX,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DOMAIN,
    SIG_MESH_PROXY_UUID,
)

_LOGGER = logging.getLogger(__name__)

_MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def _validate_mac(mac: str) -> str | None:
    """Validate a MAC address string.

    Args:
        mac: MAC address to validate.

    Returns:
        None if valid, error key string if invalid.
    """
    if not _MAC_PATTERN.match(mac):
        return "invalid_mac"
    return None


class TuyaBLEMeshConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tuya BLE Mesh."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._discovery_info: dict[str, Any] | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> dict[str, Any]:
        """Handle bluetooth discovery.

        Args:
            discovery_info: Bluetooth service info from HA bluetooth integration.

        Returns:
            Flow result dict.
        """
        address: str = discovery_info.address
        name: str = discovery_info.name or ""

        _LOGGER.info("Bluetooth discovery: %s (%s)", name, address)

        # Check if already configured
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        self._discovery_info = {
            "address": address,
            "name": name,
        }

        # Auto-detect SIG Mesh Proxy devices by service UUID
        service_uuids = getattr(discovery_info, "service_uuids", [])
        if SIG_MESH_PROXY_UUID in service_uuids:
            _LOGGER.info("SIG Mesh Proxy detected: %s", address)
            return await self.async_step_sig_plug()

        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Confirm bluetooth discovery and choose device type.

        Args:
            user_input: User confirmation input.

        Returns:
            Flow result dict.
        """
        if user_input is not None and self._discovery_info is not None:
            mac = self._discovery_info["address"]
            device_type = user_input.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)
            short_mac = mac[-8:]
            title = (
                f"BLE Mesh Plug {short_mac}"
                if device_type == DEVICE_TYPE_PLUG
                else f"BLE Mesh Light {short_mac}"
            )
            return self.async_create_entry(
                title=title,
                data={
                    CONF_MAC_ADDRESS: mac,
                    CONF_MESH_NAME: user_input.get(CONF_MESH_NAME, "out_of_mesh"),
                    CONF_MESH_PASSWORD: user_input.get(CONF_MESH_PASSWORD, "123456"),
                    CONF_VENDOR_ID: DEFAULT_VENDOR_ID,
                    CONF_DEVICE_TYPE: device_type,
                    CONF_MESH_ADDRESS: user_input.get(CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS),
                },
            )

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_TYPE, default=DEVICE_TYPE_LIGHT): vol.In(
                        {DEVICE_TYPE_LIGHT: "Light", DEVICE_TYPE_PLUG: "Plug"}
                    ),
                    vol.Optional(CONF_MESH_NAME, default="out_of_mesh"): str,
                    vol.Optional(CONF_MESH_PASSWORD, default="123456"): str,
                    vol.Optional(CONF_MESH_ADDRESS, default=DEFAULT_MESH_ADDRESS): int,
                }
            ),
            description_placeholders={
                "name": self._discovery_info.get("name", "") if self._discovery_info else "",
            },
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
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
            else:
                device_type = user_input.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)
                if device_type == DEVICE_TYPE_SIG_BRIDGE_PLUG:
                    self._discovery_info = {
                        "address": mac.upper(),
                        "name": f"SIG Bridge {mac[-8:]}",
                    }
                    return await self.async_step_sig_bridge(None)
                if device_type == DEVICE_TYPE_SIG_PLUG:
                    self._discovery_info = {
                        "address": mac.upper(),
                        "name": f"SIG Mesh {mac[-8:]}",
                    }
                    return await self.async_step_sig_plug(None)
                short = mac[-8:]
                type_label = "Plug" if device_type == DEVICE_TYPE_PLUG else "Light"
                return self.async_create_entry(
                    title=f"BLE Mesh {type_label} {short}",
                    data={
                        CONF_MAC_ADDRESS: mac.upper(),
                        CONF_MESH_NAME: user_input.get(CONF_MESH_NAME, "out_of_mesh"),
                        CONF_MESH_PASSWORD: user_input.get(CONF_MESH_PASSWORD, "123456"),
                        CONF_VENDOR_ID: user_input.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID),
                        CONF_DEVICE_TYPE: device_type,
                        CONF_MESH_ADDRESS: user_input.get(CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS),
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAC_ADDRESS): str,
                    vol.Required(CONF_DEVICE_TYPE, default=DEVICE_TYPE_LIGHT): vol.In(
                        {
                            DEVICE_TYPE_LIGHT: "Light",
                            DEVICE_TYPE_PLUG: "Plug",
                            DEVICE_TYPE_SIG_PLUG: "SIG Mesh Plug",
                            DEVICE_TYPE_SIG_BRIDGE_PLUG: "SIG Mesh Bridge Plug",
                        }
                    ),
                    vol.Optional(CONF_MESH_NAME, default="out_of_mesh"): str,
                    vol.Optional(CONF_MESH_PASSWORD, default="123456"): str,
                    vol.Optional(CONF_VENDOR_ID, default=DEFAULT_VENDOR_ID): str,
                    vol.Optional(CONF_MESH_ADDRESS, default=DEFAULT_MESH_ADDRESS): int,
                }
            ),
            description_placeholders={},
            errors=errors,
        )

    async def async_step_sig_plug(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Handle SIG Mesh plug configuration.

        Args:
            user_input: User-provided SIG Mesh parameters.

        Returns:
            Flow result dict.
        """
        if user_input is not None and self._discovery_info is not None:
            mac = self._discovery_info["address"]
            return self.async_create_entry(
                title=f"SIG Mesh {mac[-8:]}",
                data={
                    CONF_MAC_ADDRESS: mac,
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_PLUG,
                    CONF_UNICAST_TARGET: user_input.get(CONF_UNICAST_TARGET, "00aa"),
                    CONF_UNICAST_OUR: user_input.get(CONF_UNICAST_OUR, "0001"),
                    CONF_OP_ITEM_PREFIX: user_input.get(
                        CONF_OP_ITEM_PREFIX, DEFAULT_OP_ITEM_PREFIX
                    ),
                    CONF_IV_INDEX: user_input.get(CONF_IV_INDEX, DEFAULT_IV_INDEX),
                },
            )

        return self.async_show_form(
            step_id="sig_plug",
            description_placeholders={
                "name": (self._discovery_info.get("name", "") if self._discovery_info else ""),
            },
        )

    async def async_step_sig_bridge(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle SIG Mesh Bridge plug configuration.

        Args:
            user_input: User-provided bridge parameters.

        Returns:
            Flow result dict.
        """
        if user_input is not None and self._discovery_info is not None:
            mac = self._discovery_info["address"]
            return self.async_create_entry(
                title=f"SIG Bridge {mac[-8:]}",
                data={
                    CONF_MAC_ADDRESS: mac,
                    CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_BRIDGE_PLUG,
                    CONF_UNICAST_TARGET: user_input.get(CONF_UNICAST_TARGET, "00B0"),
                    CONF_BRIDGE_HOST: user_input.get(CONF_BRIDGE_HOST, ""),
                    CONF_BRIDGE_PORT: user_input.get(
                        CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT
                    ),
                },
            )

        return self.async_show_form(
            step_id="sig_bridge",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BRIDGE_HOST): str,
                    vol.Optional(
                        CONF_BRIDGE_PORT, default=DEFAULT_BRIDGE_PORT
                    ): int,
                    vol.Optional(CONF_UNICAST_TARGET, default="00B0"): str,
                }
            ),
            description_placeholders={
                "name": (
                    self._discovery_info.get("name", "")
                    if self._discovery_info
                    else ""
                ),
            },
        )
