"""Unit tests for the Tuya BLE Mesh sensor entities."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshDeviceState,
)
from custom_components.tuya_ble_mesh.sensor import (  # noqa: E402
    TuyaBLEMeshFirmwareSensor,
    TuyaBLEMeshRSSISensor,
)


def make_mock_coordinator(
    *,
    rssi: int | None = -65,
    firmware_version: str | None = "1.6",
    available: bool = True,
) -> MagicMock:
    """Create a mock coordinator."""
    coord = MagicMock()
    coord.state = TuyaBLEMeshDeviceState(
        rssi=rssi,
        firmware_version=firmware_version,
        available=available,
    )
    coord.device = MagicMock()
    coord.device.address = "DC:23:4D:21:43:A5"
    coord.add_listener = MagicMock(return_value=MagicMock())
    return coord


class TestRSSISensor:
    """Test TuyaBLEMeshRSSISensor."""

    def test_unique_id(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert "rssi" in sensor.unique_id
        assert "DC:23:4D:21:43:A5" in sensor.unique_id

    def test_name(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert "RSSI" in sensor.name

    def test_available(self) -> None:
        coord = make_mock_coordinator(available=True)
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.available is True

    def test_not_available(self) -> None:
        coord = make_mock_coordinator(available=False)
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.available is False

    def test_native_value(self) -> None:
        coord = make_mock_coordinator(rssi=-65)
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.native_value == -65

    def test_native_value_none(self) -> None:
        coord = make_mock_coordinator(rssi=None)
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.native_value is None

    def test_unit(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.native_unit_of_measurement == "dBm"

    def test_device_class(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.device_class == "signal_strength"

    def test_entity_category(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")
        assert sensor.entity_category == "diagnostic"


class TestFirmwareSensor:
    """Test TuyaBLEMeshFirmwareSensor."""

    def test_unique_id(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")
        assert "firmware" in sensor.unique_id
        assert "DC:23:4D:21:43:A5" in sensor.unique_id

    def test_name(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")
        assert "Firmware" in sensor.name

    def test_native_value(self) -> None:
        coord = make_mock_coordinator(firmware_version="1.6")
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")
        assert sensor.native_value == "1.6"

    def test_native_value_none(self) -> None:
        coord = make_mock_coordinator(firmware_version=None)
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")
        assert sensor.native_value is None

    def test_entity_category(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")
        assert sensor.entity_category == "diagnostic"


class TestSensorLifecycle:
    """Test HA lifecycle methods for sensors."""

    @pytest.mark.asyncio
    async def test_rssi_added_to_hass(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")

        await sensor.async_added_to_hass()

        coord.add_listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_rssi_removed_from_hass(self) -> None:
        coord = make_mock_coordinator()
        remove_fn = MagicMock()
        coord.add_listener.return_value = remove_fn
        sensor = TuyaBLEMeshRSSISensor(coord, "entry1")

        await sensor.async_added_to_hass()
        await sensor.async_will_remove_from_hass()

        remove_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_firmware_added_to_hass(self) -> None:
        coord = make_mock_coordinator()
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")

        await sensor.async_added_to_hass()

        coord.add_listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_firmware_removed_from_hass(self) -> None:
        coord = make_mock_coordinator()
        remove_fn = MagicMock()
        coord.add_listener.return_value = remove_fn
        sensor = TuyaBLEMeshFirmwareSensor(coord, "entry1")

        await sensor.async_added_to_hass()
        await sensor.async_will_remove_from_hass()

        remove_fn.assert_called_once()
