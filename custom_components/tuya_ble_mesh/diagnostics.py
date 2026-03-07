"""Diagnostics for the Tuya BLE Mesh integration.

Provides device diagnostics with automatic redaction of sensitive
fields (mesh_name, mesh_password).
"""

from __future__ import annotations

from typing import Any

from custom_components.tuya_ble_mesh.const import (
    CONF_APP_KEY,
    CONF_DEV_KEY,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
    CONF_NET_KEY,
)

REDACTED = "**REDACTED**"

_SENSITIVE_KEYS = frozenset(
    {CONF_MESH_NAME, CONF_MESH_PASSWORD, CONF_NET_KEY, CONF_DEV_KEY, CONF_APP_KEY}
)


def _redact_data(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of data with sensitive keys redacted."""
    return {key: REDACTED if key in _SENSITIVE_KEYS else value for key, value in data.items()}


async def async_get_config_entry_diagnostics(
    hass: Any,
    entry: Any,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "entry_id": entry.entry_id,
        "data": _redact_data(dict(entry.data)),
    }
