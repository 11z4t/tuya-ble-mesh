"""Minimal stub for homeassistant.helpers.device_registry."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

DeviceInfo = dict[str, Any]


def async_get(hass: Any) -> Any:
    """Return a mock device registry."""
    return MagicMock()
