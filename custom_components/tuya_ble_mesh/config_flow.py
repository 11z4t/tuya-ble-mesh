"""Config flow for Tuya BLE Mesh integration.

Supports bluetooth discovery (out_of_mesh*, tymesh*) and manual MAC entry.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.config_entries import ConfigFlow

from custom_components.tuya_ble_mesh.const import (
    CONF_MAC_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    DOMAIN,
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

    async def async_step_bluetooth(self, discovery_info: dict[str, Any]) -> dict[str, Any]:
        """Handle bluetooth discovery.

        Args:
            discovery_info: Discovery info from HA bluetooth integration.

        Returns:
            Flow result dict.
        """
        address: str = discovery_info.get("address", "")
        name: str = discovery_info.get("name", "")

        _LOGGER.info("Bluetooth discovery: %s (%s)", name, address)

        # Check if already configured
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        self._discovery_info = {
            "address": address,
            "name": name,
        }

        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Confirm bluetooth discovery.

        Args:
            user_input: User confirmation input.

        Returns:
            Flow result dict.
        """
        if user_input is not None and self._discovery_info is not None:
            return self.async_create_entry(
                title=self._discovery_info.get("name", "Tuya BLE Mesh"),
                data={
                    CONF_MAC_ADDRESS: self._discovery_info["address"],
                    CONF_MESH_NAME: user_input.get(CONF_MESH_NAME, "out_of_mesh"),
                    CONF_MESH_PASSWORD: user_input.get(CONF_MESH_PASSWORD, "123456"),
                },
            )

        return self.async_show_form(
            step_id="confirm",
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
                return self.async_create_entry(
                    title=f"Tuya BLE Mesh {mac[-8:]}",
                    data={
                        CONF_MAC_ADDRESS: mac.upper(),
                        CONF_MESH_NAME: user_input.get(CONF_MESH_NAME, "out_of_mesh"),
                        CONF_MESH_PASSWORD: user_input.get(CONF_MESH_PASSWORD, "123456"),
                    },
                )

        return self.async_show_form(
            step_id="user",
            errors=errors,
        )
