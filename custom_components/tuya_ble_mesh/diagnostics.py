"""Diagnostics for the Tuya BLE Mesh integration.

Provides device diagnostics with automatic redaction of sensitive
fields (mesh credentials, encryption keys, IP/MAC addresses).
Includes:
  - Connection statistics with response time percentiles
  - Error tracking and error classification
  - Protocol mode (Tuya BLE / SIG Mesh / Bridge)
  - Vendor ID and known vendor name
  - Bridge connectivity info (redacted)
  - Firmware version and feature capabilities
  - Reconnect storm state
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.tuya_ble_mesh import TuyaBLEMeshConfigEntry
from custom_components.tuya_ble_mesh.const import (
    CONF_APP_KEY,
    CONF_BRIDGE_HOST,
    CONF_DEV_KEY,
    CONF_DEVICE_TYPE,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_NET_KEY,
    CONF_VENDOR_ID,
    DEFAULT_VENDOR_ID,
    DEVICE_TYPE_LIGHT,
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
    DEVICE_TYPE_TELINK_BRIDGE_LIGHT,
    KNOWN_VENDOR_IDS,
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

# Re-export from const (single source of truth)
_KNOWN_VENDORS = KNOWN_VENDOR_IDS


def _redact_string(text: str | Any) -> str:
    """Redact IP addresses and MAC addresses from a string."""
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


def _get_protocol_mode(device_type: str) -> str:
    """Return human-readable protocol mode from device type constant."""
    protocol_map = {
        DEVICE_TYPE_LIGHT: "Tuya BLE Mesh (Telink)",
        DEVICE_TYPE_PLUG: "Tuya BLE Mesh (Telink)",
        DEVICE_TYPE_SIG_PLUG: "SIG Mesh (direct BLE)",
        DEVICE_TYPE_SIG_BRIDGE_PLUG: "SIG Mesh (HTTP bridge)",
        DEVICE_TYPE_TELINK_BRIDGE_LIGHT: "Tuya BLE Mesh (HTTP bridge)",
    }
    return protocol_map.get(device_type, f"Unknown ({device_type})")


def _get_vendor_name(vendor_id_hex: str) -> str:
    """Return human-readable vendor name for a vendor ID hex string."""
    vid = vendor_id_hex.lower().strip()
    if not vid.startswith("0x"):
        vid = f"0x{vid}"
    name = _KNOWN_VENDORS.get(vid)
    return name if name is not None else "Unknown vendor"


def _rssi_trend(history: list[tuple[float, int]]) -> str:
    """Classify RSSI trend from timestamped samples as improving/declining/stable/unknown.

    Uses a simple linear regression direction: if the slope of the last N samples
    is above +2 dBm we call it improving, below -2 dBm declining, otherwise stable.
    Requires at least 3 samples; returns "unknown" otherwise.

    Args:
        history: List of (timestamp, rssi_dbm) tuples sorted oldest-first.

    Returns:
        One of: "improving", "declining", "stable", "unknown".
    """
    if len(history) < 3:
        return "unknown"

    n = len(history)
    xs = [float(i) for i in range(n)]
    ys = [float(r) for _, r in history]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    numerator = sum((xs[i] - mean_x) * (ys[i] - mean_y) for i in range(n))
    denominator = sum((xs[i] - mean_x) ** 2 for i in range(n))
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
    device_type: str = entry.data.get(CONF_DEVICE_TYPE, "")
    vendor_id: str = entry.data.get(CONF_VENDOR_ID, DEFAULT_VENDOR_ID)

    diag: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "data": _redact_data(dict(entry.data)),
        "protocol_mode": _get_protocol_mode(device_type),
        "vendor_id": vendor_id,
        "vendor_name": _get_vendor_name(vendor_id),
    }

    # Initialize health_summary early so it's always present
    health_summary = "Unknown"
    health_details = []

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
            "last_error_class": stats.last_error_class,
            "storm_detected": stats.storm_detected,
            "recent_reconnect_count": len(stats.reconnect_times),
        }

        # Response times (avg, p50, p95, p99)
        response_times = list(stats.response_times)
        if response_times:
            percentiles = _calculate_percentiles(response_times)
            diag["response_times"] = {
                "avg_seconds": round(sum(response_times) / len(response_times), 3),
                "p50_seconds": percentiles["p50"],
                "p95_seconds": percentiles["p95"],
                "p99_seconds": percentiles["p99"],
                "sample_count": len(response_times),
                "_info": "p50=median (50% of commands faster), p95=95th percentile (only 5% slower), p99=99th percentile (worst-case latency)",
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
        is_bridge = "Bridge" in type(device).__name__
        diag["device_info"] = {
            "type": type(device).__name__,
            "address": _redact_string(device.address),  # MAC redaction
            "is_bridge": is_bridge,
        }

        # Feature capabilities (what this device type supports)
        capabilities: dict[str, bool] = {
            "on_off": True,
            "brightness": device_type in (DEVICE_TYPE_LIGHT, DEVICE_TYPE_TELINK_BRIDGE_LIGHT),
            "color_temp": device_type in (DEVICE_TYPE_LIGHT, DEVICE_TYPE_TELINK_BRIDGE_LIGHT),
            "rgb": device_type in (DEVICE_TYPE_LIGHT, DEVICE_TYPE_TELINK_BRIDGE_LIGHT),
            "power_monitoring": device_type in (DEVICE_TYPE_SIG_PLUG, DEVICE_TYPE_SIG_BRIDGE_PLUG),
            "rssi": not is_bridge,
            "firmware_version": True,
        }
        diag["capabilities"] = capabilities

        # Firmware compatibility status
        fw_version = state.firmware_version or "unknown"
        diag["firmware_compatibility"] = {
            "version": fw_version,
            "status": "compatible" if state.available else "unknown",
            "protocol": coordinator.capabilities.protocol,
        }

        # Mesh network topology info
        if is_bridge:
            diag["mesh_topology"] = {
                "mode": "bridge",
                "bridge_url": REDACTED,  # Full redaction of internal topology
                "local_ble": False,
                "bridge_type": type(device).__name__,
            }
        else:
            diag["mesh_topology"] = {
                "mode": "direct_ble",
                "local_ble": True,
            }

        # Connection quality: RSSI trend analysis from sampled history
        rssi_history = list(stats.rssi_history)
        rssi_values = [r for _, r in rssi_history]
        current_rssi = state.rssi if isinstance(state.rssi, (int, float)) else None
        diag["connection_quality"] = {
            "current_rssi": current_rssi,
            "rssi_trend": _rssi_trend(rssi_history),
            "rssi_samples": len(rssi_history),
            "avg_rssi": round(sum(rssi_values) / len(rssi_values), 1) if rssi_values else None,
            "min_rssi": min(rssi_values) if rssi_values else None,
            "max_rssi": max(rssi_values) if rssi_values else None,
            "quality_hint": (
                "good"
                if current_rssi is not None and current_rssi >= -70
                else "marginal"
                if current_rssi is not None and current_rssi >= -85
                else "poor"
                if current_rssi is not None
                else "unknown"
            ),
        }

        # Protocol health: reconnect timeline summary + error breakdown
        timeline = list(stats.reconnect_timeline)
        last_error_class = timeline[-1].error_class if timeline else None
        diag["protocol_health"] = {
            "protocol": coordinator.capabilities.protocol,
            "reconnect_timeline_events": len(timeline),
            "last_reconnect_error_class": last_error_class,
            "command_error_rate": round(
                stats.command_errors / max(stats.total_reconnects + 1, 1), 3
            ),
            "consecutive_failures": coordinator.consecutive_failures,
            "storm_threshold": coordinator.storm_threshold,
        }

        # Generate health summary based on all collected metrics
        if not state.available:
            health_summary = "Offline"
            health_details.append("Device is not responding")
        elif stats.storm_detected:
            health_summary = "Degraded (connection unstable)"
            health_details.append(f"Device reconnected {len(stats.reconnect_times)} times recently")
        elif current_rssi is not None and current_rssi < -85:
            health_summary = "Degraded (signal weak)"
            health_details.append(
                f"Signal strength {current_rssi} dBm is below recommended minimum"
            )
        elif stats.total_errors > 10 and stats.total_errors > stats.total_reconnects * 2:
            health_summary = "Degraded (high error rate)"
            health_details.append(
                f"{stats.total_errors} errors, command error rate {diag['protocol_health']['command_error_rate']}"
            )
        else:
            health_summary = "Healthy"
            if uptime_seconds and uptime_seconds > 3600:
                health_details.append(
                    f"Stable connection for {uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m"
                )
            if current_rssi and current_rssi >= -70:
                health_details.append(f"Excellent signal strength ({current_rssi} dBm)")

    # Add health summary at the top level for easy access
    diag["health_summary"] = {
        "status": health_summary,
        "details": health_details,
        "_info": "Overall device health: Healthy (working normally), Degraded (issues detected), or Offline (not responding)",
    }

    return diag
