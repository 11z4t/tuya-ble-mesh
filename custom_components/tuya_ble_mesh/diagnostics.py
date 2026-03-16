"""Diagnostics for the Tuya BLE Mesh integration.

Provides device diagnostics with automatic redaction of sensitive
fields (mesh credentials, encryption keys, IP/MAC addresses).
Includes connection statistics, response time percentiles, and
error tracking.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.tuya_ble_mesh import TuyaBLEMeshConfigEntry
from custom_components.tuya_ble_mesh.helpers import connection_quality
from custom_components.tuya_ble_mesh.const import (
    CONF_APP_KEY,
    CONF_BRIDGE_HOST,
    CONF_DEV_KEY,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_NET_KEY,
)

REDACTED = "**REDACTED**"

_SENSITIVE_KEYS = frozenset(
    {
        CONF_MESH_NAME,
        CONF_MESH_PASSWORD,
        CONF_NET_KEY,
        CONF_DEV_KEY,
        CONF_APP_KEY,
        CONF_BRIDGE_HOST,  # internal network topology
    }
)

# IP/MAC redaction patterns
_IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_MAC_PATTERN = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")


def _redact_string(text: str | Any) -> str:
    """Redact IP addresses and MAC addresses from a string."""
    # Handle non-string types gracefully (e.g., MagicMock in tests)
    if not isinstance(text, str):
        text = str(text)
    text = _IP_PATTERN.sub("xxx.xxx.xxx.xxx", text)
    text = _MAC_PATTERN.sub("XX:XX:XX:XX:XX:XX", text)
    return text


def _redact_data(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of data with sensitive keys and network info redacted."""
    redacted: dict[str, Any] = {}
    for key, value in data.items():
        if key in _SENSITIVE_KEYS:
            redacted[key] = REDACTED
        elif isinstance(value, str):
            redacted[key] = _redact_string(value)
        elif isinstance(value, dict):
            redacted[key] = _redact_data(value)
        else:
            redacted[key] = value
    return redacted


def _calculate_percentiles(times: list[float]) -> dict[str, float]:
    """Calculate response time percentiles (p50, p95, p99)."""
    if not times:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

    sorted_times = sorted(times)
    n = len(sorted_times)

    def percentile(p: float) -> float:
        k = (n - 1) * p
        f = int(k)
        c = f + 1 if f < n - 1 else f
        return sorted_times[f] + (k - f) * (sorted_times[c] - sorted_times[f])

    return {
        "p50": round(percentile(0.50), 3),
        "p95": round(percentile(0.95), 3),
        "p99": round(percentile(0.99), 3),
    }


_PROTOCOL_MODE_MAP: dict[str, str] = {
    "light": "Tuya BLE Mesh (Telink)",
    "plug": "Tuya BLE Mesh (Telink)",
    "sig_plug": "SIG Mesh (direct BLE)",
    "sig_bridge_plug": "SIG Mesh (HTTP bridge)",
    "telink_bridge_light": "Tuya BLE Mesh (HTTP bridge)",
}


def _get_protocol_mode(device_type: str) -> str:
    """Map a device type string to a human-readable protocol mode name."""
    return _PROTOCOL_MODE_MAP.get(device_type, f"Unknown ({device_type})")


_VENDOR_NAMES: dict[str, str] = {
    "1001": "Malmbergs BT Smart",
}


def _build_connection_quality(rssi: int | float | None) -> str:
    """Determine connection quality from RSSI value.

    Wrapper for diagnostics that handles edge cases (0 value, MagicMock).
    Delegates core logic to helpers.connection_quality().

    Args:
        rssi: RSSI value in dBm, or None.

    Returns:
        Quality classification: "good", "marginal", "poor", or "unknown".
    """
    if not isinstance(rssi, (int, float)) or rssi == 0:
        return "unknown"
    quality = connection_quality(rssi)
    return quality if quality is not None else "unknown"


def _build_protocol_health(
    total_errors: int | Any, consecutive_failures: int | Any, storm_detected: bool | Any
) -> dict[str, Any]:
    """Build protocol health summary.

    Args:
        total_errors: Total error count (or MagicMock in tests).
        consecutive_failures: Consecutive failure count (or MagicMock in tests).
        storm_detected: Whether error storm was detected (or MagicMock in tests).

    Returns:
        Protocol health dict with error rate classification.
    """
    # Handle non-int types gracefully (e.g., MagicMock in tests)
    errors_int = int(total_errors) if isinstance(total_errors, (int, float)) else 0
    return {
        "consecutive_failures": consecutive_failures,
        "storm_detected": bool(storm_detected),
        "error_rate": "high" if errors_int > 10 else "normal",
    }


def _get_vendor_name(vendor_id: str) -> str:
    """Map a vendor ID (hex string) to a human-readable vendor name."""
    normalized = vendor_id.strip().lower().lstrip("0x")
    return _VENDOR_NAMES.get(normalized, "Unknown vendor")


def _rssi_trend(history: list[tuple[float, int]]) -> str:
    """Analyse RSSI history and return trend as 'improving', 'declining', or 'stable'.

    Requires at least 3 samples. Returns 'unknown' with fewer samples.
    Uses linear regression slope: > 2.0 = improving, < -2.0 = declining.
    """
    if len(history) < 3:
        return "unknown"

    n = len(history)
    xs = [t for t, _ in history]
    ys = [r for _, r in history]

    x_mean = sum(xs) / n
    y_mean = sum(ys) / n

    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=False))
    denominator = sum((x - x_mean) ** 2 for x in xs)

    if denominator == 0:
        return "stable"

    slope = numerator / denominator

    if slope > 2.0:
        return "improving"
    if slope < -2.0:
        return "declining"
    return "stable"


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: TuyaBLEMeshConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    device_type = entry.data.get("device_type", "")
    vendor_id_raw = entry.data.get("vendor_id", "")
    diag: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "data": _redact_data(dict(entry.data)),
        "protocol_mode": _get_protocol_mode(device_type),
        "vendor_id": vendor_id_raw,
        "vendor_name": _get_vendor_name(str(vendor_id_raw)),
    }

    # Add coordinator state if available via runtime_data (modern pattern)
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None:
        coordinator = runtime.coordinator
        state = coordinator.state
        stats = coordinator.statistics

        # Connection statistics (uptime, reconnects, errors)
        uptime_seconds = None
        if stats.connect_time is not None:
            uptime_seconds = int(dt_util.utcnow().timestamp() - stats.connect_time)

        diag["connection_statistics"] = {
            "uptime_seconds": uptime_seconds,
            "total_reconnects": stats.total_reconnects,
            "total_errors": stats.total_errors,
            "connection_errors": stats.connection_errors,
            "command_errors": stats.command_errors,
            "last_error": _redact_string(stats.last_error) if stats.last_error else None,
            "last_error_time": (
                dt_util.utc_from_timestamp(stats.last_error_time).isoformat()
                if stats.last_error_time
                else None
            ),
        }

        # Response times (avg, p95, p99)
        response_times = list(stats.response_times)
        if response_times:
            percentiles = _calculate_percentiles(response_times)
            diag["response_times"] = {
                "avg_seconds": round(sum(response_times) / len(response_times), 3),
                "p50_seconds": percentiles["p50"],
                "p95_seconds": percentiles["p95"],
                "p99_seconds": percentiles["p99"],
                "sample_count": len(response_times),
            }
        else:
            diag["response_times"] = {
                "avg_seconds": 0.0,
                "p50_seconds": 0.0,
                "p95_seconds": 0.0,
                "p99_seconds": 0.0,
                "sample_count": 0,
            }

        # Device state
        diag["device_state"] = {
            "available": state.available,
            "is_on": state.is_on,
            "brightness": state.brightness,
            "color_temp": state.color_temp,
            "mode": state.mode,
            "rssi": state.rssi,
            "firmware_version": state.firmware_version,
            "power_w": state.power_w,
            "energy_kwh": state.energy_kwh,
        }

        # Device info (MAC redacted)
        device = coordinator.device
        diag["device_info"] = {
            "type": type(device).__name__,
            "address": _redact_string(device.address),  # MAC redaction
            "is_bridge": "Bridge" in type(device).__name__,
        }

        # Firmware compatibility status
        fw_version = state.firmware_version or "unknown"
        diag["firmware_compatibility"] = {
            "version": fw_version,
            "status": "compatible" if state.available else "unknown",
            "protocol": "SIG_Mesh" if hasattr(device, "set_seq") else "Tuya_BLE",
        }

        # Mesh network topology info (bridge-specific)
        if hasattr(device, "bridge_url"):
            # Bridge device - redact full URL but show it's using bridge
            diag["mesh_topology"] = {
                "mode": "bridge",
                "bridge_url": REDACTED,  # Full redaction of internal topology
                "local_ble": False,
            }
        else:
            diag["mesh_topology"] = {
                "mode": "direct_ble",
                "local_ble": True,
            }

        # Device capabilities summary
        caps = coordinator.capabilities
        diag["capabilities"] = {
            "has_power_monitoring": bool(getattr(caps, "has_power_monitoring", False)),
            "protocol": getattr(caps, "protocol", "unknown"),
        }

        # Connection quality based on current RSSI
        diag["connection_quality"] = _build_connection_quality(state.rssi)

        # Protocol health summary
        total_errors = getattr(stats, "total_errors", 0)
        consecutive_failures = getattr(coordinator, "consecutive_failures", 0)
        storm_detected = getattr(stats, "storm_detected", False)
        diag["protocol_health"] = _build_protocol_health(
            total_errors, consecutive_failures, storm_detected
        )

    return diag
