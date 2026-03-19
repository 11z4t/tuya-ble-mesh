"""Validation helpers for config flow."""

import ipaddress
import json
import logging
import re
from typing import Any

from aiohttp import ClientTimeout

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
        return False  # Not a numeric IP -- hostname is allowed (RFC allows LAN hosts)


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

    IV index must be a non-negative 32-bit unsigned integer (0-4294967295).

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

    Unicast addresses must be in range 0x0001-0x7FFF (SIG Mesh spec).
    Address 0x0000 is unassigned; 0x8000-0xFFFF are group addresses.

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
