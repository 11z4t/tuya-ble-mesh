"""Minimal stub for homeassistant.helpers.entity_registry (tests patch these)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


def async_get(hass: Any) -> Any:
    """Return a mock entity registry."""
    return MagicMock()


def async_entries_for_config_entry(registry: Any, config_entry_id: str) -> list[Any]:
    """Return registry entries of a config entry (none in the stub)."""
    return []
