"""Minimal stub for homeassistant.core."""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from unittest.mock import MagicMock

HomeAssistant = MagicMock


class SupportsResponse(StrEnum):
    NONE = "none"
    OPTIONAL = "optional"
    ONLY = "only"


class Event:
    def __init__(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        self.event_type = event_type
        self.data = data or {}


class State:
    def __init__(self, entity_id: str, state: str) -> None:
        self.entity_id = entity_id
        self.state = state
