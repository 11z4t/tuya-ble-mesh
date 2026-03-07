"""Diagnostics for the Tuya BLE Mesh integration.

Provides device diagnostics with automatic redaction of sensitive
fields (mesh credentials, encryption keys).
"""

from __future__ import annotations

from typing import Any

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


def _redact_data(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of data with sensitive keys redacted."""
    return {key: REDACTED if key in _SENSITIVE_KEYS else value for key, value in data.items()}


async def async_get_config_entry_diagnostics(
    hass: Any,
    entry: Any,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    diag: dict[str, Any] = {
        "entry_id": entry.entry_id,
        "data": _redact_data(dict(entry.data)),
    }

    # Add coordinator state if available via runtime_data (modern pattern)
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None:
        coordinator = runtime.coordinator
        state = coordinator.state
        diag["coordinator"] = {
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
        diag["device"] = {
            "type": type(coordinator.device).__name__,
            "address": coordinator.device.address,
        }

    return diag
