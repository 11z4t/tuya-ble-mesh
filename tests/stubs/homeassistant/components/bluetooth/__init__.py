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


def async_ble_device_from_address(
    hass: Any, address: str, connectable: bool = True
) -> Any:
    return MagicMock()
