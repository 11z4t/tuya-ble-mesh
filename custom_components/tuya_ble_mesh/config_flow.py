"""Config flow for Tuya BLE Mesh integration.

Supports bluetooth discovery (out_of_mesh*, tymesh*, SIG Mesh Proxy/Provisioning)
and manual MAC entry. Bridge connectivity is validated before creating config entries.
SIG Mesh plugs are provisioned automatically — NetKey and AppKey are generated
and the device key is derived from the ECDH provisioning exchange.

Error codes:
  bridge_unreachable      — bridge HTTP endpoint not reachable
  auth_or_mesh_mismatch   — bridge reachable but device auth fails
  unsupported_vendor      — vendor ID not recognized by bridge
  device_not_found        — device MAC not visible from bridge
  timeout                 — bridge/device operation timed out
  unknown_protocol        — protocol negotiation failed
  provisioning_failed     — SIG Mesh PB-GATT provisioning failed
  invalid_mac             — bad MAC address format
  invalid_bridge_host     — unsafe or malformed bridge host
  invalid_vendor_id       — bad vendor ID format
  invalid_credential_length — mesh name/password too long
  cannot_connect          — generic connection failure
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from homeassistant.data_entry_flow import FlowResult

from custom_components.tuya_ble_mesh.const import (
    CONF_APP_KEY,
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_COMMAND_TIMEOUT,
    CONF_DEBUG_LEVEL,
    CONF_DEV_KEY,
    CONF_DEVICE_TYPE,
    CONF_IV_INDEX,
    CONF_MAC_ADDRESS,
    CONF_MAX_RECONNECTS,
    CONF_MESH_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_NET_KEY,
    CONF_RECONNECT_STORM_THRESHOLD,
    CONF_UNICAST_OUR,
    CONF_UNICAST_TARGET,
    CONF_VENDOR_ID,
    DEFAULT_BRIDGE_PORT,
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_DEBUG_LEVEL,
    DEFAULT_IV_INDEX,
    DEFAULT_MAX_RECONNECTS,
    DEFAULT_MESH_ADDRESS,
    DEFAULT_RECONNECT_STORM_THRESHOLD,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
    DOMAIN,
    KNOWN_VENDOR_IDS,
    SIG_MESH_PROV_UUID,
    SIG_MESH_PROXY_UUID,
)

_LOGGER = logging.getLogger(__name__)

_MAC_PATTERN = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
_HEX_KEY_PATTERN = re.compile(r"^[0-9A-Fa-f]{32}$")
_VENDOR_ID_PATTERN = re.compile(r"^(?:0[xX])?[0-9A-Fa-f]{1,4}$")
_BRIDGE_TEST_TIMEOUT = 5

# Hostname-only pattern — IPs are validated via ipaddress.ip_address() below
_HOSTNAME_PATTERN = re.compile(
    r"^[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?"
    r"(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$"
)

# Unicast addresses used during provisioning
_UNICAST_PROVISIONER = 0x0001
_UNICAST_DEVICE_DEFAULT = 0x00B0

# GenericOnOff Server SIG Model ID
_MODEL_GENERIC_ONOFF_SERVER = 0x1000

# Seconds to wait for device to reboot as Proxy Service after provisioning
_POST_PROV_REBOOT_DELAY = 6.0

# Discovery flow TTL — flows expire after this duration if device stops advertising
_DISCOVERY_FLOW_TTL = 300  # 5 minutes

# Re-export from const (single source of truth)
_KNOWN_VENDOR_IDS = KNOWN_VENDOR_IDS

# Debug level choices
_DEBUG_LEVEL_CHOICES = {
    "debug": "Debug (verbose)",
    "info": "Info (normal)",
    "warning": "Warning (quiet)",
    "error": "Error (minimal)",
}

# Telink mesh UUID prefix (first 30 chars of full UUID)
_TELINK_UUID_PREFIX = "00010203-0405-0607-0809-0a0b0c0d"


def _auto_detect_device_type(discovery_info: Any) -> str:
    """Heuristically detect whether a BLE device is a plug or a light.

    Priority:
    1. Device name keywords: "plug", "socket", "outlet" → plug; "light", "bulb", "lamp" → light
    2. Manufacturer data category byte (byte index 1 of any manufacturer payload):
       0x05 or 0x07 → plug
    3. Default: light

    Args:
        discovery_info: BluetoothServiceInfoBleak or any object with
            ``.name`` (str) and ``.manufacturer_data`` (dict[int, bytes]) attributes.

    Returns:
        DEVICE_TYPE_PLUG or DEVICE_TYPE_LIGHT constant string.
    """
    name_lower = (getattr(discovery_info, "name", "") or "").lower()
    if any(kw in name_lower for kw in ("plug", "socket", "outlet")):
        return DEVICE_TYPE_PLUG
    if any(kw in name_lower for kw in ("light", "bulb", "lamp")):
        return DEVICE_TYPE_LIGHT

    # Check manufacturer data category byte
    manufacturer_data: dict[int, bytes] = getattr(discovery_info, "manufacturer_data", {}) or {}
    for payload in manufacturer_data.values():
        if len(payload) >= 2 and payload[1] in (0x05, 0x07):
            return DEVICE_TYPE_PLUG

    return DEVICE_TYPE_LIGHT


def _parse_json_body(body: str) -> dict[str, object]:
    """Parse a JSON string, returning an empty dict on failure."""
    import json

    try:
        result = json.loads(body)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _validate_hex_key(value: str) -> bool:
    """Validate a 32-char hex key string."""
    return bool(_HEX_KEY_PATTERN.match(value))


def _validate_vendor_id(value: str) -> str | None:
    """Validate a vendor ID hex string (1–4 hex digits, with optional 0x prefix).

    Returns None if valid, error key string if invalid.
    """
    stripped = value.strip()
    if not _VENDOR_ID_PATTERN.match(stripped):
        return "invalid_vendor_id"
    # Range check: strip optional 0x prefix and check <= 0xFFFF
    hex_part = stripped[2:] if stripped.lower().startswith("0x") else stripped
    if int(hex_part, 16) > 0xFFFF:
        return "invalid_vendor_id"
    return None


_PRIVATE_IP_NETS = (
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / AWS metadata
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
)


def _is_ssrf_risk(host: str) -> bool:
    """Return True if host resolves to a private/loopback address (SSRF risk).

    Checks numeric IP addresses only (hostnames are not resolved here).
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

    IP addresses (both IPv4 and IPv6) are validated strictly via
    ipaddress.ip_address(), then checked for SSRF risk.
    Hostnames are validated against RFC-1123 pattern.

    Returns None if valid, error key string if invalid.
    """
    host = host.strip()
    if not host:
        return "invalid_bridge_host"
    # Reject URLs and path-like values
    if "://" in host or "/" in host or "\\" in host:
        return "invalid_bridge_host"
    # Reject hex-encoded IPs (SSRF bypass, e.g. 0x7f000001)
    if host.lower().startswith("0x"):
        return "invalid_bridge_host"
    # Try strict IP address validation (handles both IPv4 and IPv6)
    try:
        ipaddress.ip_address(host)
        # Valid IP address — apply SSRF protection
        if _is_ssrf_risk(host):
            return "invalid_bridge_host"
        return None
    except ValueError:
        pass  # Not a numeric IP — validate as hostname

    # Hostname validation (RFC-1123)
    if not _HOSTNAME_PATTERN.match(host):
        return "invalid_bridge_host"
    return None


def _validate_mac(mac: str) -> str | None:
    """Validate a MAC address string.

    Returns None if valid, error key string if invalid.
    """
    if not _MAC_PATTERN.match(mac):
        return "invalid_mac"
    return None


def _validate_mesh_credentials(name: str, password: str) -> str | None:
    """Validate mesh name and password lengths (max 16 bytes each).

    Returns None if valid, error key string if invalid.
    """
    if len(name.encode()) > 16 or len(password.encode()) > 16:
        return "invalid_credential_length"
    return None


def _validate_mesh_credential(value: str) -> str | None:
    """Validate a single mesh credential (name or password), max 16 bytes.

    Returns None if valid, error key string if invalid.
    """
    if len(value.encode()) > 16:
        return "invalid_credential_length"
    return None


def _validate_iv_index(value: object) -> str | None:
    """Validate an IV index value (must be int in range 0–0xFFFFFFFF).

    Returns None if valid, error key string if invalid.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        return "invalid_iv_index"
    if value < 0 or value > 0xFFFFFFFF:
        return "invalid_iv_index"
    return None


_UNICAST_PATTERN = re.compile(r"^[0-9A-Fa-f]{4}$")


def _validate_unicast_address(value: str) -> str | None:
    """Validate a SIG Mesh unicast address (4 hex digits, 0001–7FFF).

    Returns None if valid, error key string if invalid.
    """
    value = value.strip()
    if not _UNICAST_PATTERN.match(value):
        return "invalid_unicast_address"
    addr = int(value, 16)
    if addr == 0x0000 or addr > 0x7FFF:
        return "invalid_unicast_address"
    return None


async def _test_bridge_with_session(hass: Any, host: str, port: int) -> bool:
    """Test bridge daemon reachability.

    Uses HA's shared aiohttp session (inject-websession pattern).

    Returns:
        True if bridge is reachable and returns status ok, False otherwise.
    """
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    url = f"http://{host}:{port}/health"
    try:
        async with session.get(url, timeout=_BRIDGE_TEST_TIMEOUT) as resp:
            if resp.status != 200:
                return False
            body = await resp.text()
            data = _parse_json_body(body)
            return data.get("status") == "ok"
    except TimeoutError:
        _LOGGER.debug("Bridge test timed out for %s:%d", host, port)
        return False
    except Exception as exc:
        _LOGGER.debug("Bridge test failed for %s:%d: %s", host, port, exc, exc_info=True)
        return False


async def _test_bridge_device_reachable(
    hass: Any, host: str, port: int, mac: str
) -> dict[str, Any]:
    """Check if a specific device MAC is visible from the bridge.

    Returns:
        dict with keys: found (bool), error (str|None), rssi (int|None)
    """
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    url = f"http://{host}:{port}/devices"
    try:
        async with session.get(url, timeout=_BRIDGE_TEST_TIMEOUT) as resp:
            if resp.status != 200:
                return {"found": False, "error": "bridge_unreachable"}
            body = await resp.text()
            data = _parse_json_body(body)
            devices = data.get("devices", [])
            if not isinstance(devices, list):
                return {"found": False, "error": "unknown_protocol"}
            mac_upper = mac.upper().replace("-", ":") if mac else ""
            for dev in devices:
                if isinstance(dev, dict):
                    dev_mac = str(dev.get("mac", "")).upper().replace("-", ":")
                    if dev_mac == mac_upper:
                        return {"found": True, "error": None, "rssi": dev.get("rssi")}
            return {"found": False, "error": "device_not_found"}
    except TimeoutError:
        return {"found": False, "error": "timeout"}
    except Exception as exc:
        _LOGGER.debug("Device reachability check failed: %s", exc, exc_info=True)
        return {"found": False, "error": "bridge_unreachable"}


class TuyaBLEMeshOptionsFlow(config_entries.OptionsFlow):  # type: ignore[misc]
    """Handle options for a Tuya BLE Mesh entry.

    Multi-step flow:
      Step 1 — init:         Basic settings (mesh credentials or SIG params).
                             Bridge devices proceed to bridge_config instead of saving.
      Step 2 — bridge_config: Bridge host/port (only for bridge device types).
      Step 3 — advanced:     Debug level, timeouts, reconnect thresholds.

    Each step shows only fields relevant to that category. Changes are
    accumulated across steps and committed at the final step via entry.options.
    Backward compatible: existing config entries work without migration.
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow with the existing config entry."""
        self._config_entry = config_entry
        # Accumulated data across steps (merged into config entry on final save)
        self._pending_data: dict[str, Any] = {}

    def _device_type(self) -> str:
        """Return the device type for this config entry."""
        return str(self._config_entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT))

    def _is_bridge_device(self) -> bool:
        """Return True if this entry uses a bridge daemon."""
        return self._device_type() in (DEVICE_TYPE_SIG_BRIDGE_PLUG, DEVICE_TYPE_TELINK_BRIDGE_LIGHT)

    # --- Step 1: basic settings ---

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Step 1 — basic settings.

        Direct BLE devices (light/plug/SIG direct): show mesh credentials / unicast
        params and save immediately (unless advanced is requested).

        Bridge devices: collect nothing here and go straight to bridge_config step
        so that settings are grouped logically.
        """
        # Bridge devices skip this step — all their config is in bridge_config
        if self._is_bridge_device():
            return await self.async_step_bridge_config(user_input)

        errors: dict[str, str] = {}
        device_type = self._device_type()

        if user_input is not None:
            go_advanced = user_input.pop("show_advanced", False)

            if device_type == DEVICE_TYPE_SIG_PLUG:
                unicast_target = user_input.get(CONF_UNICAST_TARGET, "00B0")
                unicast_error = _validate_unicast_address(unicast_target)
                if unicast_error:
                    errors[CONF_UNICAST_TARGET] = unicast_error
                iv_index = user_input.get(CONF_IV_INDEX, DEFAULT_IV_INDEX)
                iv_error = _validate_iv_index(iv_index)
                if iv_error:
                    errors[CONF_IV_INDEX] = iv_error
            else:
                # Direct Telink light/plug
                name = user_input.get(CONF_MESH_NAME, "")
                pwd = user_input.get(CONF_MESH_PASSWORD, "")
                name_error = _validate_mesh_credential(name)
                if name_error:
                    errors[CONF_MESH_NAME] = name_error
                pwd_error = _validate_mesh_credential(pwd)
                if pwd_error:
                    errors[CONF_MESH_PASSWORD] = pwd_error
                vendor_id = user_input.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID)
                vendor_error = _validate_vendor_id(vendor_id)
                if vendor_error:
                    errors[CONF_VENDOR_ID] = vendor_error

            if errors:
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._build_basic_schema(),
                    errors=errors,
                )

            self._pending_data.update(user_input)
            if go_advanced:
                return await self.async_step_advanced()
            return self._save_and_finish()

        return self.async_show_form(
            step_id="init",
            data_schema=self._build_basic_schema(),
        )

    def _build_basic_schema(self) -> vol.Schema:
        """Build the basic options schema (non-bridge device types only).

        Defaults are read from entry.options first, then entry.data (migration).
        """
        device_type = self._device_type()

        if device_type == DEVICE_TYPE_SIG_PLUG:
            return vol.Schema(
                {
                    vol.Optional(
                        CONF_UNICAST_TARGET,
                        default=self._opt(CONF_UNICAST_TARGET, "00B0"),
                    ): str,
                    vol.Optional(
                        CONF_IV_INDEX,
                        default=self._opt(CONF_IV_INDEX, DEFAULT_IV_INDEX),
                    ): int,
                    vol.Optional("show_advanced", default=False): bool,
                }
            )
        # Default: Telink direct light/plug
        return vol.Schema(
            {
                vol.Optional(
                    CONF_MESH_NAME,
                    default=self._opt(CONF_MESH_NAME, "out_of_mesh"),
                ): str,
                vol.Optional(
                    CONF_MESH_PASSWORD,
                    default=self._opt(CONF_MESH_PASSWORD, "123456"),  # pragma: allowlist secret
                ): str,
                vol.Optional(
                    CONF_VENDOR_ID,
                    default=self._opt(CONF_VENDOR_ID, DEFAULT_VENDOR_ID),
                ): str,
                vol.Optional(
                    CONF_MESH_ADDRESS,
                    default=self._opt(CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS),
                ): int,
                vol.Optional("show_advanced", default=False): bool,
            }
        )

    # --- Step 2: bridge config (bridge device types only) ---

    async def async_step_bridge_config(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2 — bridge connection settings (host, port, live validation).

        Only shown for SIG Bridge Plug and Telink Bridge Light device types.
        Validates host format and optionally tests bridge reachability.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            go_advanced = user_input.pop("show_advanced", False)
            validate = user_input.pop("validate_bridge", False)

            host = user_input.get(CONF_BRIDGE_HOST, "")
            host_error = _validate_bridge_host(host)
            if host_error:
                errors[CONF_BRIDGE_HOST] = host_error
            elif validate:
                port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
                bridge_ok = await _test_bridge_with_session(self.hass, host, port)
                if not bridge_ok:
                    errors["base"] = "cannot_connect"

            if errors:
                return self.async_show_form(
                    step_id="bridge_config",
                    data_schema=self._build_bridge_schema(),
                    errors=errors,
                )

            self._pending_data.update(user_input)
            if go_advanced:
                return await self.async_step_advanced()
            return self._save_and_finish()

        return self.async_show_form(
            step_id="bridge_config",
            data_schema=self._build_bridge_schema(),
        )

    def _build_bridge_schema(self) -> vol.Schema:
        """Build the bridge configuration schema.

        Defaults are read from entry.options first, then entry.data (migration).
        """
        return vol.Schema(
            {
                vol.Optional(
                    CONF_BRIDGE_HOST,
                    default=self._opt(CONF_BRIDGE_HOST, ""),
                ): str,
                vol.Optional(
                    CONF_BRIDGE_PORT,
                    default=self._opt(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT),
                ): int,
                vol.Optional("validate_bridge", default=False): bool,
                vol.Optional("show_advanced", default=False): bool,
            }
        )

    # --- Step 3: advanced settings ---

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3 — advanced options: debug level, timeouts, reconnect thresholds."""
        if user_input is not None:
            self._pending_data.update(user_input)
            return self._save_and_finish()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_DEBUG_LEVEL,
                    default=self._opt(CONF_DEBUG_LEVEL, DEFAULT_DEBUG_LEVEL),
                ): vol.In(_DEBUG_LEVEL_CHOICES),
                vol.Optional(
                    CONF_COMMAND_TIMEOUT,
                    default=self._opt(CONF_COMMAND_TIMEOUT, DEFAULT_COMMAND_TIMEOUT),
                ): vol.All(int, vol.Range(min=3, max=60)),
                vol.Optional(
                    CONF_MAX_RECONNECTS,
                    default=self._opt(CONF_MAX_RECONNECTS, DEFAULT_MAX_RECONNECTS),
                ): vol.All(int, vol.Range(min=0, max=100)),
                vol.Optional(
                    CONF_RECONNECT_STORM_THRESHOLD,
                    default=self._opt(
                        CONF_RECONNECT_STORM_THRESHOLD, DEFAULT_RECONNECT_STORM_THRESHOLD
                    ),
                ): vol.All(int, vol.Range(min=3, max=50)),
            }
        )

        return self.async_show_form(step_id="advanced", data_schema=schema)

    # --- helpers ---

    def _opt(self, key: str, default: Any = None) -> Any:
        """Read a setting from entry.options, falling back to entry.data (migration).

        Identity fields (MAC, device_type, keys) should never be looked up here
        and always read directly from entry.data.
        """
        opts = self._config_entry.options or {}
        if key in opts:
            return opts[key]
        return self._config_entry.data.get(key, default)

    def _save_and_finish(self) -> FlowResult:
        """Merge accumulated pending data into entry.options and finish the flow.

        Identity fields (MAC, device_type, cryptographic keys) stay in entry.data.
        All user-configurable runtime settings go into entry.options.
        """
        new_options = {**(self._config_entry.options or {}), **self._pending_data}
        return self.async_create_entry(title="", data=new_options)


class TuyaBLEMeshConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[misc, call-arg]
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

    async def async_step_bluetooth(
        self,
        discovery_info: BluetoothServiceInfoBleak,
    ) -> FlowResult:
        """Handle bluetooth discovery."""
        address: str = discovery_info.address
        name: str = discovery_info.name or ""

        _LOGGER.info("Bluetooth discovery: %s (%s)", name, address)

        # Detect device type from advertised service UUIDs
        service_uuids = getattr(discovery_info, "service_uuids", [])
        is_sig = SIG_MESH_PROV_UUID in service_uuids or SIG_MESH_PROXY_UUID in service_uuids
        rssi = getattr(discovery_info, "rssi", None)

        # Check for Telink Mesh UUID prefix
        is_telink = any(
            str(uuid).lower().startswith(_TELINK_UUID_PREFIX)
            for uuid in service_uuids
        )

        # Auto-detect device label for discovery card
        if is_sig:
            device_label = "Mesh Plug"
            device_category = "SIG Mesh"
        elif is_telink or name.startswith("tymesh"):
            # Use auto-detection to determine if it's a plug or light
            detected_type = _auto_detect_device_type(discovery_info)
            device_label = "Mesh Plug" if detected_type == DEVICE_TYPE_PLUG else "Mesh Light"
            device_category = "Telink Mesh"
        else:
            device_label = "Mesh Device"
            device_category = "Telink Mesh"

        # Set title_placeholders for discovery card with descriptive name
        short_mac = address[-8:]
        display_name = f"{device_label} {short_mac}"
        self.context["title_placeholders"] = {"mac": short_mac}

        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()

        # Check device is still reachable (stale discovery protection)
        try:
            from homeassistant.components.bluetooth import async_ble_device_from_address

            ble_device = async_ble_device_from_address(self.hass, address, connectable=False)
            if ble_device is None:
                _LOGGER.warning("Device %s not found in BLE registry, aborting stale flow", address)
                return self.async_abort(reason="device_not_available")
        except Exception:
            pass  # BLE manager not initialized (e.g., during testing), proceed normally

        self._discovery_info = {
            "address": address,
            "name": name,
            "rssi": rssi,
            "device_category": device_category,
        }

        # Auto-detect Telink device type (Light or Plug) for zero-knowledge flow
        if is_telink:
            # Re-use detection from above
            if "Plug" in device_label:
                detected_type = DEVICE_TYPE_PLUG
            else:
                detected_type = DEVICE_TYPE_LIGHT
            self._discovery_info["auto_device_type"] = detected_type
            _LOGGER.info("Telink Mesh device auto-detected as %s: %s", device_label, address)

        # SIG Mesh goes directly to provisioning
        if is_sig:
            _LOGGER.info("SIG Mesh device detected: %s", address)
            return await self.async_step_sig_plug()

        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Confirm bluetooth discovery and choose device type + mesh credentials."""
        errors: dict[str, str] = {}

        disc = self._discovery_info or {}

        # Zero-knowledge flow: if auto_device_type is set, create entry directly
        if user_input is None and disc.get("auto_device_type"):
            auto_type = disc["auto_device_type"]
            mac = disc["address"]
            short_mac = mac[-8:]
            type_label = "Plug" if auto_type == DEVICE_TYPE_PLUG else "Light"
            await self.async_set_unique_id(mac)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"BLE Mesh {type_label} {short_mac}",
                data={
                    CONF_MAC_ADDRESS: mac,
                    CONF_MESH_NAME: "out_of_mesh",
                    CONF_MESH_PASSWORD: "123456",  # pragma: allowlist secret
                    CONF_VENDOR_ID: DEFAULT_VENDOR_ID,
                    CONF_DEVICE_TYPE: auto_type,
                    CONF_MESH_ADDRESS: DEFAULT_MESH_ADDRESS,
                },
            )

        if user_input is not None and self._discovery_info is not None:
            mac = self._discovery_info["address"]
            device_type = user_input.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)
            mesh_name = user_input.get(CONF_MESH_NAME, "out_of_mesh")
            mesh_pass = user_input.get(CONF_MESH_PASSWORD, "123456")  # pragma: allowlist secret
            vendor_id = user_input.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID)

            # Validate credentials
            cred_error = _validate_mesh_credentials(mesh_name, mesh_pass)
            if cred_error:
                errors["base"] = cred_error
            elif _validate_vendor_id(vendor_id):
                errors[CONF_VENDOR_ID] = "invalid_vendor_id"
            else:
                short_mac = mac[-8:]
                type_label = "Plug" if device_type == DEVICE_TYPE_PLUG else "Light"
                return self.async_create_entry(
                    title=f"BLE Mesh {type_label} {short_mac}",
                    data={
                        CONF_MAC_ADDRESS: mac,
                        CONF_MESH_NAME: mesh_name,
                        CONF_MESH_PASSWORD: mesh_pass,
                        CONF_VENDOR_ID: vendor_id,
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
                    vol.Optional(CONF_MESH_PASSWORD, default="123456"): str,  # pragma: allowlist secret
                    vol.Optional(CONF_VENDOR_ID, default=DEFAULT_VENDOR_ID): str,
                    vol.Optional(CONF_MESH_ADDRESS, default=DEFAULT_MESH_ADDRESS): int,
                }
            ),
            description_placeholders={
                "name": disc.get("name", "Unknown"),
                "mac": disc.get("address", ""),
                "rssi": str(disc.get("rssi", "?")),
                "category": disc.get("device_category", ""),
            },
            errors=errors,
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle manual setup."""
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

                # Validate mesh credentials (per-field errors)
                mesh_name = user_input.get(CONF_MESH_NAME, "out_of_mesh")
                mesh_pass = user_input.get(CONF_MESH_PASSWORD, "123456")  # pragma: allowlist secret
                name_error = _validate_mesh_credential(mesh_name)
                if name_error:
                    errors[CONF_MESH_NAME] = name_error
                pass_error = _validate_mesh_credential(mesh_pass)
                if pass_error:
                    errors[CONF_MESH_PASSWORD] = pass_error
                vendor_id = user_input.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID)
                vendor_error = _validate_vendor_id(vendor_id)
                if vendor_error:
                    errors[CONF_VENDOR_ID] = vendor_error

                if not errors:
                    short = mac[-8:]
                    type_label = "Plug" if device_type == DEVICE_TYPE_PLUG else "Light"
                    await self.async_set_unique_id(mac.upper())
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"BLE Mesh {type_label} {short}",
                        data={
                            CONF_MAC_ADDRESS: mac.upper(),
                            CONF_MESH_NAME: mesh_name,
                            CONF_MESH_PASSWORD: mesh_pass,
                            CONF_VENDOR_ID: user_input.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID),
                            CONF_DEVICE_TYPE: device_type,
                            CONF_MESH_ADDRESS: user_input.get(
                                CONF_MESH_ADDRESS, DEFAULT_MESH_ADDRESS
                            ),
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
                    vol.Optional(CONF_MESH_PASSWORD, default="123456"): str,  # pragma: allowlist secret
                    vol.Optional(CONF_VENDOR_ID, default=DEFAULT_VENDOR_ID): str,
                    vol.Optional(CONF_MESH_ADDRESS, default=DEFAULT_MESH_ADDRESS): int,
                }
            ),
            description_placeholders={},
            errors=errors,
        )

    async def async_step_sig_plug(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle SIG Mesh plug — auto-provisions and generates all keys.

        The device is provisioned via PB-GATT (Service UUID 0x1827).
        A random NetKey and AppKey are generated. The DevKey is derived
        from the ECDH exchange during provisioning. After provisioning,
        AppKey is added and bound to the GenericOnOff Server model via
        the Proxy Service (UUID 0x1828).
        """
        errors: dict[str, str] = {}

        if user_input is not None and self._discovery_info is not None:
            mac = self._discovery_info["address"]

            # Fast-fail: if device is not visible in the BLE registry, abort early
            # rather than spending 5 retry attempts on an invisible device.
            from homeassistant.components import bluetooth as _ha_bt

            _ble = _ha_bt.async_ble_device_from_address(self.hass, mac.upper(), connectable=True)
            if _ble is None:
                _ble = _ha_bt.async_ble_device_from_address(self.hass, mac.upper(), connectable=False)
            if _ble is None:
                _LOGGER.warning("SIG Mesh device %s not found via BLE registry — aborting", mac)
                return self.async_abort(reason="device_not_found")

            try:
                net_key_hex, dev_key_hex, app_key_hex = await self._run_provision(mac)
            except asyncio.TimeoutError:
                _LOGGER.warning("Provisioning timed out for %s", mac)
                errors["base"] = "timeout"
            except Exception as exc:
                # Map specific exceptions to error keys
                error_key = "provisioning_failed"
                try:
                    from tuya_ble_mesh.exceptions import (                        DeviceNotFoundError,
                        ProvisioningError,
                    )
                    from tuya_ble_mesh.exceptions import TimeoutError as MeshTimeoutError
                    if isinstance(exc, DeviceNotFoundError):
                        error_key = "device_not_found"
                    elif isinstance(exc, MeshTimeoutError):
                        error_key = "timeout"
                    elif isinstance(exc, ProvisioningError):
                        error_key = "provisioning_failed"
                except ImportError:
                    pass
                _LOGGER.warning(
                    "Provisioning failed for %s: %s: %s",
                    mac,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                errors["base"] = error_key
            else:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"SIG Mesh {mac[-8:]}",
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
        """Generate keys, provision the device, configure AppKey and model bind.

        Phase 1: PB-GATT provisioning (Service 0x1827).
        Phase 2: Wait for device to reboot into Proxy Service (0x1828).
        Phase 3: Add AppKey and bind to GenericOnOff Server model.

        Returns:
            Tuple of (net_key_hex, dev_key_hex, app_key_hex).

        Raises:
            ProvisioningError: If PB-GATT provisioning fails.
        """
        from bleak import BleakClient
        from bleak_retry_connector import establish_connection
        from homeassistant.components import bluetooth as ha_bluetooth
        from tuya_ble_mesh.secrets import DictSecretsManager
        from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice
        from tuya_ble_mesh.sig_mesh_provisioner import SIGMeshProvisioner

        # Generate fresh random keys (SECURITY: never logged)
        net_key = os.urandom(16)
        app_key = os.urandom(16)

        _LOGGER.info(
            "Auto-provisioning SIG Mesh device %s (unicast=0x%04X)",
            mac,
            _UNICAST_DEVICE_DEFAULT,
        )

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
            appkey_ok = await device.send_config_appkey_add(app_key)
            if not appkey_ok:
                _LOGGER.warning("AppKey Add returned non-success for %s", mac)
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
                "Post-provisioning config failed for %s (device provisioned but AppKey not bound)",
                mac,
                exc_info=True,
            )
        finally:
            await device.disconnect()

        return net_key.hex(), result.dev_key.hex(), app_key.hex()

    async def async_step_sig_bridge(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle SIG Mesh Bridge plug configuration with live bridge validation."""
        errors: dict[str, str] = {}
        if user_input is not None and self._discovery_info is not None:
            host = user_input.get(CONF_BRIDGE_HOST, "")
            port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
            unicast_target = user_input.get(CONF_UNICAST_TARGET, "00B0")

            # Validate host and unicast address together (accumulate errors)
            host_error = _validate_bridge_host(host)
            if host_error:
                errors[CONF_BRIDGE_HOST] = host_error
            unicast_error = _validate_unicast_address(unicast_target)
            if unicast_error:
                errors[CONF_UNICAST_TARGET] = unicast_error

            if not errors:
                # Test bridge reachability
                bridge_result = await _test_bridge_with_session(self.hass, host, port)
                if not bridge_result:
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
        """Handle Telink Bridge light configuration with live bridge validation."""
        errors: dict[str, str] = {}
        if user_input is not None and self._discovery_info is not None:
            host = user_input.get(CONF_BRIDGE_HOST, "")
            port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
            host_error = _validate_bridge_host(host)
            if host_error:
                errors[CONF_BRIDGE_HOST] = host_error
            else:
                bridge_result = await _test_bridge_with_session(self.hass, host, port)
                if not bridge_result:
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

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle user-initiated reconfiguration of connection settings.

        Allows updating bridge host/port (bridge devices) or mesh name/
        password/vendor-ID (direct BLE devices) without removing and
        re-adding the config entry. The underlying MAC address and device
        type are preserved unchanged.
        """
        entry = self.hass.config_entries.async_get_entry(self.context.get("entry_id", ""))
        if entry is None:
            return self.async_abort(reason="entry_not_found")

        device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)
        is_bridge = device_type in (DEVICE_TYPE_SIG_BRIDGE_PLUG, DEVICE_TYPE_TELINK_BRIDGE_LIGHT)
        errors: dict[str, str] = {}

        if user_input is not None:
            if is_bridge:
                host = user_input.get(CONF_BRIDGE_HOST, "").strip()
                host_error = _validate_bridge_host(host)
                if host_error:
                    errors[CONF_BRIDGE_HOST] = host_error
                else:
                    port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
                    bridge_ok = await _test_bridge_with_session(self.hass, host, port)
                    if not bridge_ok:
                        errors["base"] = "cannot_connect"
            else:
                name = user_input.get(CONF_MESH_NAME, "")
                pwd = user_input.get(CONF_MESH_PASSWORD, "")
                cred_error = _validate_mesh_credentials(name, pwd)
                if cred_error:
                    errors["base"] = cred_error
                if not cred_error and CONF_VENDOR_ID in user_input:
                    vendor_error = _validate_vendor_id(user_input[CONF_VENDOR_ID])
                    if vendor_error:
                        errors[CONF_VENDOR_ID] = vendor_error

            if not errors:
                new_data = {**entry.data, **user_input}
                self.hass.config_entries.async_update_entry(entry, data=new_data)
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")

        # Build schema with current entry data as defaults
        if is_bridge:
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
        else:
            schema = vol.Schema(
                {
                    vol.Optional(
                        CONF_MESH_NAME,
                        default=entry.data.get(CONF_MESH_NAME, ""),
                    ): str,
                    vol.Optional(
                        CONF_MESH_PASSWORD,
                        default=entry.data.get(CONF_MESH_PASSWORD, ""),
                    ): str,
                    vol.Optional(
                        CONF_VENDOR_ID,
                        default=entry.data.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID),
                    ): str,
                }
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauth when mesh credentials fail.

        Triggered by the coordinator when auth errors occur (e.g. wrong mesh
        password after credentials are rotated on the device).
        """
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Re-enter mesh credentials after authentication failure."""
        errors: dict[str, str] = {}

        entry = self.hass.config_entries.async_get_entry(self.context.get("entry_id", ""))

        if user_input is not None and entry is not None:
            device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_LIGHT)

            # Validate bridge host if it's a bridge device
            if device_type in (DEVICE_TYPE_SIG_BRIDGE_PLUG, DEVICE_TYPE_TELINK_BRIDGE_LIGHT):
                host = user_input.get(CONF_BRIDGE_HOST, "")
                host_error = _validate_bridge_host(host)
                if host_error:
                    errors[CONF_BRIDGE_HOST] = host_error
                else:
                    port = user_input.get(CONF_BRIDGE_PORT, DEFAULT_BRIDGE_PORT)
                    bridge_result = await _test_bridge_with_session(self.hass, host, port)
                    if not bridge_result:
                        errors["base"] = "cannot_connect"
            else:
                name = user_input.get(CONF_MESH_NAME, "")
                pwd = user_input.get(CONF_MESH_PASSWORD, "")
                cred_error = _validate_mesh_credentials(name, pwd)
                if cred_error:
                    errors["base"] = cred_error

            if not errors:
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
