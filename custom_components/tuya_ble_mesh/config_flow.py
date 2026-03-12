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
import json
import logging
import os
import re
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlow

if TYPE_CHECKING:
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from homeassistant.data_entry_flow import FlowResult

import contextlib

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
    DEFAULT_FACTORY_MESH_NAME,
    DEFAULT_FACTORY_MESH_PASSWORD,
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

# Maximum age (seconds) of a BLE advertisement before we consider the device gone.
# Prevents stale discovery notifications for devices that stopped advertising.
_STALE_ADVERTISEMENT_SECONDS = 180  # 3 minutes

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
# 15s: some SIG Mesh devices need >10s to boot + start Proxy advertising
_POST_PROV_REBOOT_DELAY = 15.0

# BLE operation timeouts (seconds)
_BLE_SCAN_TIMEOUT = 10.0  # find_device_by_address timeout
_BLE_SCAN_WAIT_TIMEOUT = 15.0  # wait_for wrapper around scan
_BLE_CLIENT_TIMEOUT = 20.0  # BleakClient constructor timeout
_BLE_CONNECT_TIMEOUT = 20.0  # client.connect() timeout (no pairing)
_BLE_CONNECT_PAIR_TIMEOUT = 25.0  # client.connect(pair=True) timeout
_BLUETOOTHCTL_TIMEOUT = 5.0  # bluetoothctl subprocess timeout
_BLUEZ_SETTLE_DELAY = 1.0  # delay after bluetoothctl remove
_PAIRING_RETRY_DELAY = 2.0  # delay before retry after pairing failure

# SIG Mesh configuration delays (seconds)
_POST_PROV_RETRY_DELAY_MULTIPLIER = 5.0  # extra delay per retry attempt
_CONFIG_COMMAND_DELAY = 1.0  # delay between config commands (AppKey Add -> Model Bind)

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
    try:
        result = json.loads(body)
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _validate_hex_key(value: str) -> bool:
    """Validate a 32-char hex key string."""
    return bool(_HEX_KEY_PATTERN.match(value))


def _validate_vendor_id(value: str) -> str | None:
    """Validate a vendor ID hex string (1-4 hex digits, with optional 0x prefix).

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
    """Validate an IV index value (must be int in range 0-0xFFFFFFFF).

    Returns None if valid, error key string if invalid.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        return "invalid_iv_index"
    if value < 0 or value > 0xFFFFFFFF:
        return "invalid_iv_index"
    return None


_UNICAST_PATTERN = re.compile(r"^[0-9A-Fa-f]{4}$")


def _validate_unicast_address(value: str) -> str | None:
    """Validate a SIG Mesh unicast address (4 hex digits, 0001-7FFF).

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
                    default=self._opt(CONF_MESH_NAME, DEFAULT_FACTORY_MESH_NAME),
                ): str,
                vol.Optional(
                    CONF_MESH_PASSWORD,
                    # pragma: allowlist secret
                    default=self._opt(CONF_MESH_PASSWORD, DEFAULT_FACTORY_MESH_PASSWORD),
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
        return self.async_create_entry(data=new_options)


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

    def _is_device_stale(self, address: str) -> bool:
        """Check if a BLE device has stopped advertising (stale).

        Uses HA's bluetooth API to verify the device has a recent
        advertisement. Returns True if the device should be considered
        gone (no recent advertisements within _STALE_ADVERTISEMENT_SECONDS).

        Args:
            address: BLE MAC address to check.

        Returns:
            True if the device is stale / not advertising, False if active.
        """
        try:
            from homeassistant.components.bluetooth import async_last_service_info

            service_info = async_last_service_info(self.hass, address, connectable=False)
            if service_info is None:
                _LOGGER.debug("No service info for %s — device is stale", address)
                return True

            # BluetoothServiceInfoBleak.time is monotonic clock
            import time as _time

            age = _time.monotonic() - service_info.time
            if age > _STALE_ADVERTISEMENT_SECONDS:
                _LOGGER.debug(
                    "Device %s last seen %.0fs ago (limit %ds) — stale",
                    address,
                    age,
                    _STALE_ADVERTISEMENT_SECONDS,
                )
                return True
            return False
        except (ImportError, AttributeError):
            # async_last_service_info not available (old HA or tests) — fall back
            try:
                from homeassistant.components.bluetooth import (
                    async_ble_device_from_address,
                )

                return async_ble_device_from_address(self.hass, address, connectable=False) is None
            except Exception:
                return False  # Can't determine — assume not stale

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
            device_label = "BLE Plug"
            device_category = "SIG Mesh"
        elif is_telink or name.startswith("tymesh"):
            detected_type = _auto_detect_device_type(discovery_info)
            device_label = "BLE Plug" if detected_type == DEVICE_TYPE_PLUG else "BLE Light"
            device_category = "Telink Mesh"
        else:
            # out_of_mesh* devices — auto-detect type, default to Light
            detected_type = _auto_detect_device_type(discovery_info)
            device_label = "BLE Plug" if detected_type == DEVICE_TYPE_PLUG else "BLE Light"
            device_category = "Telink Mesh"

        # Set title_placeholders for discovery card with descriptive name
        short_mac = address[-8:]
        display_name = f"{device_label} {short_mac}"
        self.context["title_placeholders"] = {
            "name": display_name,
            "mac": short_mac,
            "address": address,
        }

        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured(updates={})

        # Check device is still actively advertising (stale discovery protection)
        if self._is_device_stale(address):
            _LOGGER.warning(
                "Device %s not actively advertising, aborting stale discovery", address
            )
            return self.async_abort(reason="device_not_available")

        self._discovery_info = {
            "address": address,
            "name": name,
            "display_name": display_name,
            "rssi": rssi,
            "device_category": device_category,
        }

        # Auto-detect device type for all non-SIG devices
        if not is_sig:
            detected_type = DEVICE_TYPE_PLUG if "Plug" in device_label else DEVICE_TYPE_LIGHT
            self._discovery_info["auto_device_type"] = detected_type
            _LOGGER.info("Mesh device auto-detected as %s: %s", device_label, address)

        # SIG Mesh goes directly to provisioning
        if is_sig:
            _LOGGER.info("SIG Mesh device detected: %s", address)
            return await self.async_step_sig_plug()

        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Confirm bluetooth discovery — no fields, just confirm and pair.

        When user clicks Submit:
        1. Try direct BLE pairing with factory defaults
        2. If direct fails → auto-test bridge at known host (192.168.1.124:8099)
        3. If bridge works → create entry as bridge device
        4. If nothing works → show error
        """
        errors: dict[str, str] = {}
        disc = self._discovery_info or {}

        # Re-check that device is still advertising before showing form
        if disc.get("address") and self._is_device_stale(disc["address"]):
            _LOGGER.warning(
                "Device %s stopped advertising, aborting confirm flow",
                disc["address"],
            )
            return self.async_abort(reason="device_not_available")

        if user_input is not None and self._discovery_info is not None:
            mac = self._discovery_info["address"]
            auto_type = disc.get("auto_device_type", DEVICE_TYPE_LIGHT)
            short_mac = mac[-8:]
            type_label = "Plug" if auto_type == DEVICE_TYPE_PLUG else "Light"

            # Step 1: Pair via local BLE adapter (hci0, NOT via ESPHome proxy)
            # Discovery happens via ESPHome proxy (passive scan), but pairing
            # requires full GATT connection — only the local USB adapter can do this.
            # After pairing, normal control can go via proxy.
            # We force adapter="hci0" to bypass HA's habluetooth which would
            # route through the proxy (causing hangs on Telink GATT).
            direct_ok = False

            # Enable max debug logging for pairing
            import logging as _logging

            for _log_name in (
                "tuya_ble_mesh",
                "tuya_ble_mesh.provisioner",
                "tuya_ble_mesh.connection",
                "tuya_ble_mesh.device",
                "tuya_ble_mesh.protocol",
                "tuya_ble_mesh.crypto",
                "bleak",
                "bleak.backends",
                "custom_components.tuya_ble_mesh",
            ):
                _logging.getLogger(_log_name).setLevel(_logging.DEBUG)
            _LOGGER.setLevel(_logging.DEBUG)

            _LOGGER.warning(
                "[PAIR] ===== PAIRING START for %s (v0.25.20) =====", mac
            )
            _LOGGER.warning(
                "[PAIR] auto_type=%s, type_label=%s, short_mac=%s",
                auto_type,
                type_label,
                short_mac,
            )

            try:
                _LOGGER.warning("[PAIR] Step 1: Importing REAL bleak (bypassing habluetooth)...")
                # CRITICAL: HA's habluetooth monkey-patches bleak.BleakClient with
                # HaBleakClientWrapper which intercepts connect() and routes through
                # ESPHome proxy. We must import the REAL BlueZ backend directly
                # for the BleakClient — habluetooth only patches the client, not the scanner.
                from bleak import BleakScanner as _RawBleakScanner
                from bleak.backends.bluezdbus.client import BleakClientBlueZDBus as _RawBleakClient

                _LOGGER.warning("[PAIR] Step 2: Importing provisioner...")
                from tuya_ble_mesh.provisioner import provision

                vid_hex = DEFAULT_VENDOR_ID.replace("0x", "").replace("0X", "")
                _LOGGER.warning(
                    "[PAIR] Step 3: vid_hex=%s, mesh_name=%s",
                    vid_hex,
                    DEFAULT_FACTORY_MESH_NAME,
                )

                # Step 1a: Scan for device on LOCAL adapter (hci0)
                _LOGGER.warning(
                    "[PAIR] Step 4: BleakScanner.find_device_by_address"
                    "(%s, timeout=%s, adapter=hci0)...",
                    mac,
                    _BLE_SCAN_TIMEOUT,
                )
                ble_device = await asyncio.wait_for(
                    _RawBleakScanner.find_device_by_address(
                        mac, timeout=_BLE_SCAN_TIMEOUT, adapter="hci0"
                    ),
                    timeout=_BLE_SCAN_WAIT_TIMEOUT,
                )

                if ble_device is None:
                    _LOGGER.warning(
                        "[PAIR] Step 4 FAILED: Device %s not found on hci0 scan", mac
                    )
                    raise TimeoutError(f"Device {mac} not found on local BLE adapter")

                _LOGGER.warning(
                    "[PAIR] Step 4 OK: Found %s (name=%s, rssi=%s, details=%s)",
                    mac,
                    ble_device.name,
                    getattr(ble_device, "rssi", "?"),
                    type(ble_device).__name__,
                )

                # Step 4b: Remove stale BlueZ device entry to avoid connect conflicts
                _LOGGER.warning("[PAIR] Step 4b: Removing stale BlueZ device %s...", mac)
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "bluetoothctl", "remove", mac,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(proc.wait(), timeout=_BLUETOOTHCTL_TIMEOUT)
                    _LOGGER.warning("[PAIR] Step 4b: BlueZ device removed (or didn't exist)")
                    await asyncio.sleep(_BLUEZ_SETTLE_DELAY)  # Let BlueZ settle
                except asyncio.CancelledError:
                    raise
                except Exception as rm_exc:
                    _LOGGER.warning("[PAIR] Step 4b: Could not remove BlueZ device: %s", rm_exc)

                # Step 4c: Re-scan for fresh BLEDevice after removal
                _LOGGER.warning("[PAIR] Step 4c: Re-scanning for %s...", mac)
                ble_device = await asyncio.wait_for(
                    _RawBleakScanner.find_device_by_address(
                        mac, timeout=_BLE_SCAN_TIMEOUT, adapter="hci0"
                    ),
                    timeout=_BLE_SCAN_WAIT_TIMEOUT,
                )
                if ble_device is None:
                    _LOGGER.warning("[PAIR] Step 4c FAILED: Device %s not found after re-scan", mac)
                    raise TimeoutError(f"Device {mac} not found after BlueZ cleanup")
                _LOGGER.warning("[PAIR] Step 4c OK: Fresh device found")

                # Step 5: Connect via REAL BlueZ backend (bypass habluetooth wrapper)
                _LOGGER.warning(
                    "[PAIR] Step 5: BleakClientBlueZDBus(%s, adapter=hci0, timeout=%s)...",
                    mac,
                    _BLE_CLIENT_TIMEOUT,
                )
                client = _RawBleakClient(
                    ble_device, adapter="hci0", timeout=_BLE_CLIENT_TIMEOUT
                )
                _LOGGER.warning(
                    "[PAIR] Step 5a: client.connect() starting (raw BlueZ, no habluetooth)..."
                )
                try:
                    await asyncio.wait_for(client.connect(pair=False), timeout=_BLE_CONNECT_TIMEOUT)
                except TypeError:
                    _LOGGER.warning("[PAIR] Step 5a: pair= not supported, retrying without")
                    await asyncio.wait_for(client.connect(), timeout=_BLE_CONNECT_TIMEOUT)
                except TimeoutError:
                    _LOGGER.warning(
                        "[PAIR] Step 5a: connect(pair=False) timed out, retrying with pair=True"
                    )
                    with contextlib.suppress(Exception):
                        await client.disconnect()
                    # Re-remove + re-scan before retry
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            "bluetoothctl", "remove", mac,
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=asyncio.subprocess.DEVNULL,
                        )
                        await asyncio.wait_for(proc.wait(), timeout=_BLUETOOTHCTL_TIMEOUT)
                        await asyncio.sleep(_PAIRING_RETRY_DELAY)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass
                    ble_device = await asyncio.wait_for(
                        _RawBleakScanner.find_device_by_address(
                            mac, timeout=_BLE_SCAN_TIMEOUT, adapter="hci0"
                        ),
                        timeout=_BLE_SCAN_WAIT_TIMEOUT,
                    )
                    if ble_device is None:
                        raise TimeoutError(
                            f"Device {mac} not found after second cleanup"
                        ) from None
                    client = _RawBleakClient(
                        ble_device, adapter="hci0", timeout=_BLE_CLIENT_TIMEOUT
                    )
                    await asyncio.wait_for(
                        client.connect(pair=True), timeout=_BLE_CONNECT_PAIR_TIMEOUT
                    )
                _LOGGER.warning(
                    "[PAIR] Step 5 OK: GATT connected (is_connected=%s, mtu=%s)",
                    client.is_connected,
                    getattr(client, "mtu_size", "?"),
                )

                # Log discovered services
                try:
                    services = client.services
                    if services:
                        for svc in services:
                            _LOGGER.warning(
                                "[PAIR] Service: %s (%s)",
                                svc.uuid,
                                svc.description if hasattr(svc, "description") else "",
                            )
                except Exception as svc_exc:
                    _LOGGER.warning("[PAIR] Could not list services: %s", svc_exc)

                # Step 1c: Provision (Telink mesh session key exchange)
                _LOGGER.warning(
                    "[PAIR] Step 6: provision(client, mesh_name, mesh_password)..."
                )
                session_key = await asyncio.wait_for(
                    provision(
                        client,
                        DEFAULT_FACTORY_MESH_NAME.encode("utf-8"),
                        DEFAULT_FACTORY_MESH_PASSWORD.encode("utf-8"),  # pragma: allowlist secret
                    ),
                    timeout=15.0,
                )
                _LOGGER.warning(
                    "[PAIR] Step 6 OK: Provisioning succeeded (key length: %d)",
                    len(session_key),
                )

                # Disconnect — the coordinator will reconnect later
                _LOGGER.warning("[PAIR] Step 7: Disconnecting...")
                await client.disconnect()
                _LOGGER.warning("[PAIR] Step 7 OK: Disconnected — pairing complete")
                direct_ok = True

            except TimeoutError as te:
                _LOGGER.warning(
                    "[PAIR] TIMEOUT during pairing of %s: %s", mac, te, exc_info=True
                )
            except ImportError as ie:
                _LOGGER.warning(
                    "[PAIR] IMPORT ERROR during pairing: %s", ie, exc_info=True
                )
            except Exception as exc:
                _LOGGER.warning(
                    "[PAIR] EXCEPTION during pairing of %s: %s (%s)",
                    mac,
                    exc,
                    type(exc).__name__,
                    exc_info=True,
                )

            if direct_ok:
                return self.async_create_entry(
                    title=f"BLE {type_label} {short_mac}",
                    data={
                        CONF_MAC_ADDRESS: mac,
                        CONF_MESH_NAME: DEFAULT_FACTORY_MESH_NAME,
                        # pragma: allowlist secret
                        CONF_MESH_PASSWORD: DEFAULT_FACTORY_MESH_PASSWORD,
                        CONF_VENDOR_ID: DEFAULT_VENDOR_ID,
                        CONF_DEVICE_TYPE: auto_type,
                        CONF_MESH_ADDRESS: DEFAULT_MESH_ADDRESS,
                    },
                )

            # Step 2: Auto-test bridge at known address
            bridge_host = "192.168.1.124"
            bridge_port = DEFAULT_BRIDGE_PORT
            bridge_ok = await _test_bridge_with_session(self.hass, bridge_host, bridge_port)

            if bridge_ok:
                _LOGGER.info(
                    "Bridge at %s:%d reachable — creating bridge entry for %s",
                    bridge_host,
                    bridge_port,
                    mac,
                )
                return self.async_create_entry(
                    title=f"BLE {type_label} {short_mac}",
                    data={
                        CONF_MAC_ADDRESS: mac,
                        CONF_DEVICE_TYPE: DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
                        CONF_BRIDGE_HOST: bridge_host,
                        CONF_BRIDGE_PORT: bridge_port,
                    },
                )

            # Neither direct BLE nor bridge worked
            _LOGGER.warning("Neither direct BLE nor bridge pairing succeeded for %s", mac)
            errors["base"] = "pairing_failed"

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "name": disc.get("display_name", disc.get("name", "Unknown")),
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
                mesh_name = user_input.get(CONF_MESH_NAME, DEFAULT_FACTORY_MESH_NAME)
                # pragma: allowlist secret
                mesh_pass = user_input.get(CONF_MESH_PASSWORD, DEFAULT_FACTORY_MESH_PASSWORD)
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
                        title=f"BLE {type_label} {short}",
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
                    vol.Optional(CONF_MESH_NAME, default=DEFAULT_FACTORY_MESH_NAME): str,
                    # pragma: allowlist secret
                    vol.Optional(
                        CONF_MESH_PASSWORD, default=DEFAULT_FACTORY_MESH_PASSWORD
                    ): str,
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

        # Re-check that device is still advertising before showing form
        disc = self._discovery_info or {}
        if disc.get("address") and self._is_device_stale(disc["address"]):
            _LOGGER.warning(
                "Device %s stopped advertising, aborting SIG provisioning flow",
                disc["address"],
            )
            return self.async_abort(reason="device_not_available")

        if user_input is not None and self._discovery_info is not None:
            mac = self._discovery_info["address"]

            # Device visibility is confirmed by discovery — no need to re-check
            # via HA BLE registry (which may route through ESPHome proxy).

            try:
                net_key_hex, dev_key_hex, app_key_hex = await self._run_provision(mac)
            except TimeoutError:
                _LOGGER.warning("Provisioning timed out for %s", mac)
                errors["base"] = "timeout"
            except Exception as exc:
                # Map specific exceptions to error keys
                error_key = "provisioning_failed"
                try:
                    from tuya_ble_mesh.exceptions import (
                        DeviceNotFoundError,
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
                    title=f"BLE Plug {mac[-8:]}",
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
        from tuya_ble_mesh.secrets import DictSecretsManager
        from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice
        from tuya_ble_mesh.sig_mesh_provisioner import SIGMeshProvisioner

        # Generate fresh random keys (SECURITY: never logged)
        net_key = os.urandom(16)
        app_key = os.urandom(16)

        # Enable max debug logging for SIG provisioning
        import logging as _logging

        for _log_name in (
            "tuya_ble_mesh",
            "tuya_ble_mesh.sig_mesh_provisioner",
            "tuya_ble_mesh.sig_mesh_device",
            "bleak",
        ):
            _logging.getLogger(_log_name).setLevel(_logging.DEBUG)

        _LOGGER.warning(
            "[SIG-PAIR] ===== SIG PROVISIONING START for %s (v0.25.20) =====", mac
        )
        _LOGGER.warning(
            "[SIG-PAIR] unicast=0x%04X, iv_index=%d, adapter=hci0",
            _UNICAST_DEVICE_DEFAULT,
            DEFAULT_IV_INDEX,
        )

        # Phase 1: PB-GATT provisioning — direct bleak, not via HA BLE manager/proxy
        # Provisioning requires full GATT connection that only the local adapter can do.
        # PLAT-408: Increased timeout and retries for more reliable provisioning
        provisioner = SIGMeshProvisioner(
            net_key=net_key,
            app_key=app_key,
            unicast_addr=_UNICAST_DEVICE_DEFAULT,
            iv_index=DEFAULT_IV_INDEX,
            adapter="hci0",  # Force local adapter, bypass ESPHome proxy
        )
        result = await provisioner.provision(
            mac,
            timeout=20.0,  # Increased from default 15.0
            max_retries=8,  # Increased from default 5
        )

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
            ble_device_callback=None,  # Force direct BLE, no HA proxy
            adapter="hci0",  # Force local adapter for post-prov config
        )
        # PLAT-408: Retry post-provisioning up to 5 times with increasing delay.
        # The device may take varying time to start Proxy Service after reboot.
        post_prov_ok = False
        for attempt in range(5):  # Increased from 3 to 5 attempts
            try:
                if attempt > 0:
                    extra_delay = _POST_PROV_RETRY_DELAY_MULTIPLIER * attempt
                    _LOGGER.warning(
                        "[SIG-PAIR] Post-prov retry %d, waiting %.0fs extra...",
                        attempt + 1, extra_delay,
                    )
                    await asyncio.sleep(extra_delay)

                # PLAT-408: Increased timeout and retries for post-prov connect
                await device.connect(timeout=25.0, max_retries=5)  # Increased from 20s/3 to 25s/5

                appkey_ok = await device.send_config_appkey_add(app_key)
                if not appkey_ok:
                    _LOGGER.warning("AppKey Add returned non-success for %s", mac)

                await asyncio.sleep(_CONFIG_COMMAND_DELAY)

                bind_ok = await device.send_config_model_app_bind(
                    _UNICAST_DEVICE_DEFAULT, 0, _MODEL_GENERIC_ONOFF_SERVER
                )
                if not bind_ok:
                    _LOGGER.warning(
                        "Model App Bind returned non-success for %s (model=0x%04X)",
                        mac,
                        _MODEL_GENERIC_ONOFF_SERVER,
                    )

                # Send timestamp sync to finalize Tuya vendor setup
                try:
                    from tuya_ble_mesh.sig_mesh_protocol import tuya_vendor_timestamp_response
                    ts_payload = tuya_vendor_timestamp_response()
                    await device.send_vendor_command(ts_payload)
                    _LOGGER.warning("[SIG-PAIR] Timestamp sync sent to %s", mac)
                except Exception as ts_exc:
                    _LOGGER.warning("[SIG-PAIR] Timestamp sync failed: %s", ts_exc)

                post_prov_ok = True
                _LOGGER.warning(
                    "[SIG-PAIR] Post-provisioning config OK for %s (appkey=%s, bind=%s)",
                    mac, appkey_ok, bind_ok,
                )
                break

            except Exception:
                _LOGGER.warning(
                    "[SIG-PAIR] Post-prov attempt %d failed for %s",
                    attempt + 1, mac,
                    exc_info=True,
                )
                with contextlib.suppress(Exception):
                    await device.disconnect()

        if not post_prov_ok:
            _LOGGER.warning(
                "[SIG-PAIR] Post-provisioning FAILED after 5 attempts for %s "
                "(device provisioned but AppKey not bound — device may need factory reset)",
                mac,
            )

        with contextlib.suppress(Exception):
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
        entry = self.hass.config_entries.async_get_entry(
            self.context.get("config_entry_id") or self.context.get("entry_id", "")
        )
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
                return self.async_update_reload_and_abort(
                    entry,
                    data=new_data,
                    reason="reconfigure_successful",
                )

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

        entry = self.hass.config_entries.async_get_entry(
            self.context.get("config_entry_id") or self.context.get("entry_id", "")
        )

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
                # Split user_input: identity/credentials → data, tunables → options
                identity_keys = {
                    CONF_MAC_ADDRESS, CONF_DEVICE_TYPE, CONF_UNICAST_TARGET,
                    CONF_UNICAST_OUR, CONF_NET_KEY, CONF_DEV_KEY, CONF_APP_KEY,
                    CONF_MESH_NAME, CONF_MESH_PASSWORD, CONF_VENDOR_ID,
                    CONF_IV_INDEX, CONF_MESH_ADDRESS,
                }
                data_updates = {k: v for k, v in user_input.items() if k in identity_keys}
                options_updates = {k: v for k, v in user_input.items() if k not in identity_keys}

                if data_updates:
                    new_data = {**entry.data, **data_updates}
                    self.hass.config_entries.async_update_entry(entry, data=new_data)
                if options_updates:
                    new_options = {**(entry.options or {}), **options_updates}
                    self.hass.config_entries.async_update_entry(entry, options=new_options)

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
                    vol.Optional(CONF_MESH_NAME, default=DEFAULT_FACTORY_MESH_NAME): str,
                    vol.Optional(CONF_MESH_PASSWORD, default=""): str,
                }
            )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
        )
