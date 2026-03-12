"""Unit tests for binary_sensor.py — TuyaBLEMeshConnectivitySensor."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from homeassistant.components.binary_sensor import (  # noqa: E402
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers.entity import EntityCategory  # noqa: E402

from custom_components.tuya_ble_mesh.binary_sensor import (  # noqa: E402
    TuyaBLEMeshConnectivitySensor,
    async_setup_entry,
)
from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshCoordinator,
    TuyaBLEMeshDeviceState,
)


def make_mock_coordinator(*, available: bool = True) -> MagicMock:
    """Return a minimal coordinator mock."""
    coord = MagicMock(spec=TuyaBLEMeshCoordinator)
    coord.state = TuyaBLEMeshDeviceState(available=available)
    coord.device = MagicMock()
    coord.device.address = "DC:23:4D:21:43:A5"
    coord.add_listener = MagicMock(return_value=MagicMock())
    coord.async_add_listener = MagicMock(return_value=MagicMock())
    return coord


# --- Construction ---


class TestConnectivitySensorConstruction:
    """Test TuyaBLEMeshConnectivitySensor init."""

    def test_unique_id(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshConnectivitySensor(coord, "entry1")
        assert sensor.unique_id == "DC:23:4D:21:43:A5_connectivity"

    def test_unique_id_with_device_info(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshConnectivitySensor(coord, "entry1", device_info=MagicMock())
        assert sensor.unique_id == "DC:23:4D:21:43:A5_connectivity"

    def test_device_class(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshConnectivitySensor(coord, "entry1")
        assert sensor._attr_device_class == BinarySensorDeviceClass.CONNECTIVITY

    def test_entity_category(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshConnectivitySensor(coord, "entry1")
        assert sensor._attr_entity_category == EntityCategory.DIAGNOSTIC

    def test_translation_key(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshConnectivitySensor(coord, "entry1")
        assert sensor._attr_translation_key == "connectivity"

    def test_inherits_binary_sensor_entity(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshConnectivitySensor(coord, "entry1")
        assert isinstance(sensor, BinarySensorEntity)


# --- available property ---


class TestConnectivitySensorAvailable:
    """Test the always-available behaviour."""

    def test_always_available_when_connected(self) -> None:
        coord = make_mock_coordinator(available=True)
        sensor = TuyaBLEMeshConnectivitySensor(coord, "entry1")
        assert sensor.available is True

    def test_always_available_when_disconnected(self) -> None:
        """available must stay True even when BLE is down — sensor must not hide."""
        coord = make_mock_coordinator(available=False)
        sensor = TuyaBLEMeshConnectivitySensor(coord, "entry1")
        assert sensor.available is True


# --- is_on property ---


class TestConnectivitySensorIsOn:
    """Test is_on reflects coordinator.state.available."""

    def test_is_on_when_connected(self) -> None:
        coord = make_mock_coordinator(available=True)
        sensor = TuyaBLEMeshConnectivitySensor(coord, "entry1")
        assert sensor.is_on is True

    def test_is_off_when_disconnected(self) -> None:
        coord = make_mock_coordinator(available=False)
        sensor = TuyaBLEMeshConnectivitySensor(coord, "entry1")
        assert sensor.is_on is False

    def test_is_on_tracks_state_changes(self) -> None:
        """is_on reads from live coordinator.state, not cached."""
        coord = make_mock_coordinator(available=True)
        sensor = TuyaBLEMeshConnectivitySensor(coord, "entry1")
        assert sensor.is_on is True

        coord.state = TuyaBLEMeshDeviceState(available=False)
        assert sensor.is_on is False

        coord.state = TuyaBLEMeshDeviceState(available=True)
        assert sensor.is_on is True


# --- async_setup_entry ---


class TestBinarySensorSetupEntry:
    """Test async_setup_entry creates expected entities."""

    @pytest.mark.asyncio
    async def test_setup_creates_connectivity_sensor(self) -> None:
        coord = make_mock_coordinator()
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.runtime_data.coordinator = coord
        entry.runtime_data.device_info = MagicMock()

        added: list[object] = []
        await async_setup_entry(MagicMock(), entry, added.extend)

        assert len(added) == 1
        assert isinstance(added[0], TuyaBLEMeshConnectivitySensor)

    @pytest.mark.asyncio
    async def test_setup_entry_id_used_in_unique_id(self) -> None:
        coord = make_mock_coordinator()
        entry = MagicMock()
        entry.entry_id = "my_entry"
        entry.runtime_data.coordinator = coord
        entry.runtime_data.device_info = None

        added: list[object] = []
        await async_setup_entry(MagicMock(), entry, added.extend)

        sensor = added[0]
        assert isinstance(sensor, TuyaBLEMeshConnectivitySensor)
        assert sensor.unique_id == "DC:23:4D:21:43:A5_connectivity"
