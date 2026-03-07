"""Config flow for Tuya BLE Mesh integration.

Supports bluetooth discovery (out_of_mesh*, tymesh*) and manual MAC entry.
Bridge connectivity is validated before creating config entries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

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
    SIG_MESH_PROXY_UUID,
)

_LOGGER = logging.getLogger(__name__)

_MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_HEX_KEY_PATTERN = re.compile(r"^[0-9A-Fa-f]{32}$")
_BRIDGE_TEST_TIMEOUT = 5.0
_MESH_KEYS_PATH = "/tmp/mesh_keys.json"


def _load_mesh_key_defaults() -> dict[str, str]:
    """Try to load default mesh keys from /tmp/mesh_keys.json.

    Returns:
        Dict with net_key, dev_key, app_key defaults (empty strings if missing).
    """
    defaults: dict[str, str] = {"net_key": "", "dev_key": "", "app_key": ""}
    try:
        import os

        if os.path.exists(_MESH_KEYS_PATH):
            with open(_MESH_KEYS_PATH) as f:
                data = json.load(f)
            defaults["net_key"] = data.get("net_key", "")
            defaults["dev_key"] = data.get("dev_key", "")
            defaults["app_key"] = data.get("app_key", "")
    except Exception:
        _LOGGER.debug("Could not load mesh key defaults", exc_info=True)
    return defaults


def _validate_hex_key(value: str) -> bool:
    """Validate a 32-char hex key string."""
    return bool(_HEX_KEY_PATTERN.match(value))


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


async def _test_bridge(host: str, port: int) -> bool:
    """Test if bridge daemon is reachable.

    Args:
        host: Bridge hostname/IP.
        port: Bridge port.

    Returns:
        True if bridge responds with status ok.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=_BRIDGE_TEST_TIMEOUT,
        )
        try:
            request = f"GET /health HTTP/1.1\r\nHost: {host}\r\n\r\n"
            writer.write(request.encode())
            await writer.drain()
            response = await asyncio.wait_for(
                reader.read(4096),
                timeout=_BRIDGE_TEST_TIMEOUT,
            )
            body = response.decode("utf-8", errors="replace")
            parts = body.split("\r\n\r\n", 1)
            if len(parts) > 1:
                data = json.loads(parts[1])
                return data.get("status") == "ok"
        finally:
            writer.close()
    except Exception:
        _LOGGER.debug("Bridge test failed for %s:%d", host, port, exc_info=True)
    return False


class TuyaBLEMeshConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tuya BLE Mesh."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._discovery_info: dict[str, Any] | None = None

    async def async_step_bluetooth(
        self,
        discovery_info: BluetoothServiceInfoBleak,
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
                await self.async_set_unique_id(mac.upper())
                if device_type == DEVICE_TYPE_SIG_BRIDGE_PLUG:
                    self._discovery_info = {
                        "address": mac.upper(),
                        "name": f"SIG Bridge Plug {mac[-8:]}",
                    }
                    return await self.async_step_sig_bridge(None)
                if device_type == DEVICE_TYPE_TELINK_BRIDGE_LIGHT:
                    self._discovery_info = {
                        "address": mac.upper(),
                        "name": f"Telink Bridge Light {mac[-8:]}",
                    }
                    return await self.async_step_telink_bridge(None)
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
                            DEVICE_TYPE_SIG_BRIDGE_PLUG: "SIG Mesh Plug (via bridge)",
                            DEVICE_TYPE_TELINK_BRIDGE_LIGHT: "Telink Light (via bridge)",
                            DEVICE_TYPE_LIGHT: "Light (direct BLE, requires adapter)",
                            DEVICE_TYPE_PLUG: "Plug (direct BLE, requires adapter)",
                            DEVICE_TYPE_SIG_PLUG: "SIG Mesh Plug (direct BLE)",
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
        errors: dict[str, str] = {}
        if user_input is not None and self._discovery_info is not None:
            # Validate hex keys
            net_key = user_input.get(CONF_NET_KEY, "")
            dev_key = user_input.get(CONF_DEV_KEY, "")
            app_key = user_input.get(CONF_APP_KEY, "")
            if not _validate_hex_key(net_key):
                errors[CONF_NET_KEY] = "invalid_key"
            if not _validate_hex_key(dev_key):
                errors[CONF_DEV_KEY] = "invalid_key"
            if not _validate_hex_key(app_key):
                errors[CONF_APP_KEY] = "invalid_key"

            if not errors:
                mac = self._discovery_info["address"]
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"SIG Mesh {mac[-8:]}",
                    data={
                        CONF_MAC_ADDRESS: mac,
                        CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_PLUG,
                        CONF_UNICAST_TARGET: user_input.get(CONF_UNICAST_TARGET, "00B0"),
                        CONF_UNICAST_OUR: user_input.get(CONF_UNICAST_OUR, "0001"),
                        CONF_IV_INDEX: user_input.get(CONF_IV_INDEX, DEFAULT_IV_INDEX),
                        CONF_NET_KEY: net_key,
                        CONF_DEV_KEY: dev_key,
                        CONF_APP_KEY: app_key,
                    },
                )

        key_defaults = _load_mesh_key_defaults()
        return self.async_show_form(
            step_id="sig_plug",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_UNICAST_TARGET, default="00B0"): str,
                    vol.Optional(CONF_UNICAST_OUR, default="0001"): str,
                    vol.Optional(CONF_IV_INDEX, default=DEFAULT_IV_INDEX): int,
                    vol.Required(
                        CONF_NET_KEY,
                        default=key_defaults["net_key"],
                    ): str,
                    vol.Required(
                        CONF_DEV_KEY,
                        default=key_defaults["dev_key"],
                    ): str,
                    vol.Required(
                        CONF_APP_KEY,
                        default=key_defaults["app_key"],
                    ): str,
                }
            ),
            description_placeholders={
                "name": (self._discovery_info.get("name", "") if self._discovery_info else ""),
            },
            errors=errors,
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
        errors: dict[str, str] = {}
        if user_input is not None and self._discovery_info is not None:
            host = user_input.get(CONF_BRIDGE_HOST, "")
            port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
            if not await _test_bridge(host, port):
                errors["base"] = "cannot_connect"
            else:
                mac = self._discovery_info["address"]
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"SIG Bridge Plug {mac[-8:]}",
                    data={
                        CONF_MAC_ADDRESS: mac,
                        CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_BRIDGE_PLUG,
                        CONF_UNICAST_TARGET: user_input.get(CONF_UNICAST_TARGET, "00B0"),
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
            },
            errors=errors,
        )

    async def async_step_telink_bridge(
        self, user_input: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
            if not await _test_bridge(host, port):
                errors["base"] = "cannot_connect"
            else:
                mac = self._discovery_info["address"]
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Telink Bridge Light {mac[-8:]}",
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
            },
            errors=errors,
        )
