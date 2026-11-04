"""Minimal stub for homeassistant.components.bluetooth."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock


@dataclass
class BluetoothServiceInfoBleak:
    name: str = ""
    address: str = ""
    rssi: int = -65
    manufacturer_data: dict[int, bytes] = field(default_factory=dict)
    service_uuids: list[str] = field(default_factory=list)
    service_data: dict[str, bytes] = field(default_factory=dict)
    source: str = "local"
    advertisement: Any = None
    device: Any = None
    connectable: bool = True
    time: float = 0.0


def async_ble_device_from_address(hass: Any, address: str, connectable: bool = True) -> Any:
    return MagicMock()


class BluetoothCallbackMatcher(dict):  # type: ignore[type-arg]
    """Minimal stub of HA's BluetoothCallbackMatcher (a TypedDict)."""


class BluetoothScanningMode:
    """Minimal stub of HA's BluetoothScanningMode enum."""

    PASSIVE = "passive"
    ACTIVE = "active"


async def async_process_advertisements(
    hass: Any, callback: Any, match_dict: Any, mode: Any, timeout: float
) -> Any:
    """Stub: real HA waits for a matching advertisement; tests patch this."""
    raise TimeoutError


def async_last_service_info(hass: Any, address: str, connectable: bool = True) -> Any:
    """Stub: no advertisement history; tests patch this."""
    return None
