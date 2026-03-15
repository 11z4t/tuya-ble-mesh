"""Config flow for Tuya BLE Mesh integration.

Supports bluetooth discovery (out_of_mesh*, tymesh*, SIG Mesh Proxy/Provisioning)
and manual MAC entry. Bridge connectivity is validated before creating config entries.
SIG Mesh plugs are provisioned automatically using a secure key exchange.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Ensure lib/tuya_ble_mesh is importable from config flow context
_BUNDLED_LIB = str(Path(__file__).resolve().parent / "lib")
_DEV_LIB = str(Path(__file__).resolve().parent.parent.parent / "lib")
for _lib_dir in (_BUNDLED_LIB, _DEV_LIB):
    if Path(_lib_dir).is_dir() and _lib_dir not in sys.path:
        sys.path.insert(0, _lib_dir)
        break

import voluptuous as vol  # noqa: E402
from aiohttp import ClientTimeout  # noqa: E402
from homeassistant import config_entries  # noqa: E402
from homeassistant.config_entries import ConfigFlow  # noqa: E402

# Module-level imports for patching in tests
try:
    from tuya_ble_mesh.sig_mesh_bridge import SIGMeshBridgeDevice  # type: ignore[import-not-found]
    from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice  # type: ignore[import-not-found]
except ImportError:
    SIGMeshBridgeDevice = None  # type: ignore[assignment,misc]
    SIGMeshDevice = None  # type: ignore[assignment,misc]

try:
    from bleak import BleakScanner as _BleakScanner

    find_device_by_address = _BleakScanner.find_device_by_address
except (ImportError, AttributeError):
    find_device_by_address = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from homeassistant.data_entry_flow import FlowResult

from custom_components.tuya_ble_mesh.const import (  # noqa: E402
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
    SIG_MESH_PROV_UUID,
    SIG_MESH_PROXY_UUID,
)

_LOGGER = logging.getLogger(__name__)

_MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_HEX_KEY_PATTERN = re.compile(r"^[0-9A-Fa-f]{32}$")
_VENDOR_ID_PATTERN = re.compile(r"^(?:0[xX])?[0-9A-Fa-f]{1,4}$")
_BRIDGE_TEST_TIMEOUT = 5

# Telink BLE Mesh uses 16-byte buffers for name and password (silently truncated).
# Enforce this limit at input time so users are not surprised.
_MESH_CREDENTIAL_MAX_LEN = 16

# Allowed bridge host pattern: IPv4, IPv6, or hostname
_BRIDGE_HOST_PATTERN = re.compile(
    r"^(?:"
    r"(?:\d{1,3}\.){3}\d{1,3}"  # IPv4
    r"|(?:[0-9a-fA-F:]+)"  # IPv6
    r"|(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*)"  # hostname
    r")$"
)

# Unicast addresses used during provisioning
_UNICAST_PROVISIONER = 0x0001
_UNICAST_DEVICE_DEFAULT = 0x00B0

# GenericOnOff Server SIG Model ID
_MODEL_GENERIC_ONOFF_SERVER = 0x1000

# Seconds to wait for device to reboot as Proxy Service after provisioning
_POST_PROV_REBOOT_DELAY = 6.0


def _rssi_to_signal_quality(rssi: int | None) -> str:
    """Convert RSSI dBm value to a human-readable signal quality label.

    Args:
        rssi: Signal strength in dBm (negative integer) or None if unknown.

    Returns:
        Human-readable label: Excellent, Good, Fair, Weak, or Unknown.
    """
    if rssi is None:
        return "Unknown"
    if rssi >= -65:
        return "Excellent"
    if rssi >= -75:
        return "Good"
    if rssi >= -85:
        return "Fair"
    return "Weak"


def _parse_json_body(body: str) -> dict[str, object]:
    """Parse a JSON string, returning an empty dict on failure.

    Args:
        body: JSON string to parse.

    Returns:
        Parsed dict, or empty dict on parse error.
    """
    import json

    try:
        result = json.loads(body)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _validate_hex_key(value: str) -> bool:
    """Validate a 32-char hex key string.

    Args:
        value: String to check.

    Returns:
        True if value matches 32 lowercase or uppercase hex characters.
    """
    return bool(_HEX_KEY_PATTERN.match(value))


_PRIVATE_IP_NETS = (
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / AWS metadata
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
)


def _is_ssrf_risk(host: str) -> bool:
    """Return True if host resolves to a private/loopback address (SSRF risk).

    Checks numeric IP addresses only (hostnames are not resolved here).

    Args:
        host: Host string to check.

    Returns:
        True if the host is a known SSRF risk.
    """
    # Reject hex-encoded IP (e.g. "0x7f000001")
    if host.startswith("0x") or host.startswith("0X"):
        return True
    try:
        addr = ipaddress.ip_address(host)
        return any(addr in net for net in _PRIVATE_IP_NETS)
    except ValueError:
        return False  # Not a numeric IP — hostname is allowed (RFC allows LAN hosts)


def _validate_bridge_host(host: str) -> str | None:
    """Validate bridge host is a plain hostname or IP, not a URL.

    Rejects URLs (containing ://), paths (/), empty strings, and private/
    loopback IP addresses to prevent SSRF via crafted bridge_host values.

    Args:
        host: Bridge host string to validate.

    Returns:
        None if valid, error key string if invalid.
    """
    host = host.strip()
    if not host:
        return "invalid_bridge_host"
    # Reject URLs and path-like values
    if "://" in host or "/" in host or "\\" in host:
        return "invalid_bridge_host"
    if not _BRIDGE_HOST_PATTERN.match(host):
        return "invalid_bridge_host"
    # SSRF protection: reject private/loopback IPs
    if _is_ssrf_risk(host):
        return "invalid_bridge_host"
    return None


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


def _validate_mesh_credential(value: str) -> str | None:
    """Validate a mesh name or password.

    Telink BLE Mesh silently truncates credentials to 16 bytes.
    Reject inputs that are too long to avoid unexpected behaviour.

    Args:
        value: Credential string to validate.

    Returns:
        None if valid, error key string if invalid.
    """
    if len(value.encode("utf-8")) > _MESH_CREDENTIAL_MAX_LEN:
        return "invalid_credential_length"
    return None


def _validate_vendor_id(value: str) -> str | None:
    """Validate a vendor ID string (e.g. '0x1001' or '1001').

    Args:
        value: Vendor ID string to validate.

    Returns:
        None if valid, error key string if invalid.
    """
    value = value.strip()
    if not _VENDOR_ID_PATTERN.match(value):
        return "invalid_vendor_id"
    return None


def _validate_iv_index(value: int) -> str | None:
    """Validate a SIG Mesh IV index value.

    IV index must be a non-negative 32-bit unsigned integer (0–4294967295).

    Args:
        value: IV index to validate.

    Returns:
        None if valid, error key string if invalid.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        return "invalid_iv_index"
    if not 0 <= value <= 0xFFFFFFFF:
        return "invalid_iv_index"
    return None


_UNICAST_ADDR_PATTERN = re.compile(r"^[0-9A-Fa-f]{4}$")


def _validate_unicast_address(value: str) -> str | None:
    """Validate a 4-character hex unicast address for SIG Mesh.

    Unicast addresses must be in range 0x0001–0x7FFF (SIG Mesh spec).
    Address 0x0000 is unassigned; 0x8000–0xFFFF are group addresses.

    Args:
        value: Unicast address string (e.g. ``"00B0"``).

    Returns:
        None if valid, error key string if invalid.
    """
    value = value.strip()
    if not _UNICAST_ADDR_PATTERN.match(value):
        return "invalid_unicast_address"
    parsed = int(value, 16)
    if not 0x0001 <= parsed <= 0x7FFF:
        return "invalid_unicast_address"
    return None


async def _test_bridge_with_session(hass: Any, host: str, port: int) -> bool:
    """Test if bridge daemon is reachable using HA's aiohttp websession.

    Uses HA's shared aiohttp session (inject-websession pattern) so that
    HA can properly manage the session lifecycle and TLS settings.

    Args:
        hass: Home Assistant instance (provides shared aiohttp session).
        host: Bridge hostname/IP.
        port: Bridge port.

    Returns:
        True if bridge responds with status ok.
    """
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    url = f"http://{host}:{port}/health"
    try:
        async with session.get(url, timeout=ClientTimeout(total=_BRIDGE_TEST_TIMEOUT)) as resp:
            if resp.status != 200:
                return False
            body = await resp.text()
            return _parse_json_body(body).get("status") == "ok"
    except Exception:
        _LOGGER.debug("Bridge test failed for %s:%d", host, port, exc_info=True)
    return False


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
                        "123456",  # pragma: allowlist secret
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
        # Stored provisioning keys set by _run_provision
        self._prov_net_key: str = ""
        self._prov_dev_key: str = ""
        self._prov_app_key: str = ""

    async def async_step_bluetooth(  # type: ignore[override]
        self,
        discovery_info: BluetoothServiceInfoBleak,
    ) -> FlowResult:
        """Handle bluetooth discovery.

        Args:
            discovery_info: Bluetooth service info from HA bluetooth integration.

        Returns:
            Flow result dict.
        """
        from homeassistant.components.bluetooth import async_ble_device_from_address

        address: str = discovery_info.address
        name: str = discovery_info.name or ""

        _LOGGER.info("Bluetooth discovery: %s (%s)", name, address)

        # Check if already configured
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        # PLAT-509: Check if device is still advertising (stale flow protection)
        # If the device is not currently available in HA's bluetooth stack, ignore the discovery.
        # This prevents stale discovery flows from persisting after a device stops advertising.
        try:
            ble_device = async_ble_device_from_address(self.hass, address, connectable=False)
            if ble_device is None:
                _LOGGER.debug(
                    "Ignoring stale discovery for %s (device no longer advertising)", address
                )
                return self.async_abort(reason="device_not_available")
        except RuntimeError:
            # BluetoothManager not initialized (e.g. in tests) — skip stale check
            _LOGGER.debug("BluetoothManager not available, skipping stale check for %s", address)

        # Detect human-readable device category from service UUIDs
        device_category = "Smart Plug" if any(
            u in getattr(discovery_info, "service_uuids", [])
            for u in (SIG_MESH_PROV_UUID, SIG_MESH_PROXY_UUID)
        ) else "LED Light"
        rssi = getattr(discovery_info, "rssi", None)

        # PLAT-510: Auto-detect device type based on service UUIDs
        service_uuids = getattr(discovery_info, "service_uuids", [])
        auto_device_type = None

        if SIG_MESH_PROV_UUID in service_uuids or SIG_MESH_PROXY_UUID in service_uuids:
            # SIG Mesh device → Plug
            auto_device_type = DEVICE_TYPE_SIG_PLUG
        elif any(
            uuid.startswith("00010203-0405-0607-0809-0a0b0c0d") for uuid in service_uuids
        ):
            # Telink mesh UUID prefix → Light
            auto_device_type = DEVICE_TYPE_LIGHT

        self._discovery_info = {
            "address": address,
            "name": name,
            "rssi": rssi,
            "device_category": device_category,
            "auto_device_type": auto_device_type,
        }

        # Auto-detect SIG Mesh devices by service UUID.
        # 0x1827 = Provisioning Service (unprovisioned device)
        # 0x1828 = Proxy Service (already provisioned)
        if auto_device_type == DEVICE_TYPE_SIG_PLUG:
            _LOGGER.info("SIG Mesh device detected: %s", address)
            return await self.async_step_sig_plug()

        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Confirm bluetooth discovery and choose device type.

        PLAT-511: Zero-knowledge config flow — if device type is auto-detected
        and user provides no custom values, create entry directly without showing form.

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
                f"Smart Plug {short_mac}"
                if device_type == DEVICE_TYPE_PLUG
                else f"LED Light {short_mac}"
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

        # PLAT-510: Use auto-detected device type as default if available
        # PLAT-511: If device type is confidently auto-detected, skip form and create entry directly
        default_device_type = DEVICE_TYPE_LIGHT
        auto_detected = False
        if self._discovery_info:
            auto_type = self._discovery_info.get("auto_device_type")
            if auto_type in (DEVICE_TYPE_LIGHT, DEVICE_TYPE_PLUG):
                default_device_type = auto_type
                auto_detected = True

        # PLAT-511: Zero-knowledge flow — if type is auto-detected, create entry with defaults
        if auto_detected and self._discovery_info:
            mac = self._discovery_info["address"]
            short_mac = mac[-8:]
            title = (
                f"Smart Plug {short_mac}"
                if default_device_type == DEVICE_TYPE_PLUG
                else f"LED Light {short_mac}"
            )
            _LOGGER.info(
                "Zero-knowledge config: auto-detected %s, creating entry with defaults",
                default_device_type,
            )
            return self.async_create_entry(
                title=title,
                data={
                    CONF_MAC_ADDRESS: mac,
                    CONF_MESH_NAME: "out_of_mesh",
                    CONF_MESH_PASSWORD: "123456",
                    CONF_VENDOR_ID: DEFAULT_VENDOR_ID,
                    CONF_DEVICE_TYPE: default_device_type,
                    CONF_MESH_ADDRESS: DEFAULT_MESH_ADDRESS,
                },
            )

        # UX: Hide internal credentials in normal mode — only show in advanced options
        confirm_schema: dict[object, object] = {
            vol.Required(CONF_DEVICE_TYPE, default=default_device_type): vol.In(
                {DEVICE_TYPE_LIGHT: "Light", DEVICE_TYPE_PLUG: "Plug"}
            ),
        }
        if self.show_advanced_options:
            confirm_schema[vol.Optional(CONF_MESH_NAME, default="out_of_mesh")] = str
            confirm_schema[vol.Optional(CONF_MESH_PASSWORD, default="123456")] = str  # pragma: allowlist secret
            confirm_schema[vol.Optional(CONF_MESH_ADDRESS, default=DEFAULT_MESH_ADDRESS)] = int

        rssi_raw = self._discovery_info.get("rssi") if self._discovery_info else None
        rssi_int = int(rssi_raw) if rssi_raw is not None else None
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(confirm_schema),
            description_placeholders={
                "name": (
                    self._discovery_info.get("name", "Unknown")
                    if self._discovery_info
                    else "Unknown"
                ),
                # Human-readable signal quality label (used in current strings.json)
                "signal_quality": _rssi_to_signal_quality(rssi_int),
                "category": (
                    self._discovery_info.get("device_category", "Smart Device")
                    if self._discovery_info
                    else "Smart Device"
                ),
                # Legacy placeholders kept for older translated strings that reference them
                "rssi": str(rssi_raw) if rssi_raw is not None else "?",
                "mac": (
                    self._discovery_info.get("address", "") if self._discovery_info else ""
                ),
            },
        )

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
                except Exception:  # noqa: BLE001
                    pass

                device_type = user_input.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)
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
                if device_type == DEVICE_TYPE_SIG_PLUG:
                    self._discovery_info = {
                        "address": mac.upper(),
                        "name": f"Smart Plug {mac[-8:]}",
                    }
                    return await self.async_step_sig_plug(None)
                short = mac[-8:]
                type_label = "Smart Plug" if device_type == DEVICE_TYPE_PLUG else "LED Light"
                await self.async_set_unique_id(mac.upper())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{type_label} {short}",
                    data={
                        CONF_MAC_ADDRESS: mac.upper(),
                        CONF_MESH_NAME: user_input.get(CONF_MESH_NAME, "out_of_mesh"),
                        CONF_MESH_PASSWORD: user_input.get(CONF_MESH_PASSWORD, "123456"),
                        CONF_VENDOR_ID: user_input.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID),
                        CONF_DEVICE_TYPE: device_type,
                        CONF_MESH_ADDRESS: user_input.get(CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS),
                    },
                )

        # UX-1.4: 3 user-facing device types (SIG types auto-detected via Bluetooth discovery)
        # UX-1.5: Progressive disclosure — advanced fields shown only in HA advanced mode
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
            schema_dict[vol.Optional(CONF_MESH_PASSWORD, default="123456")] = str  # pragma: allowlist secret
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
                        _LOGGER.warning(
                            "Provisioning handshake failed for %s: %s", mac, exc
                        )
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
