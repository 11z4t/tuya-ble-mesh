"""Unit tests for Tuya BLE Mesh binary sensor entities."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from homeassistant.components.binary_sensor import BinarySensorDeviceClass  # noqa: E402
from homeassistant.helpers.entity import EntityCategory  # noqa: E402

from custom_components.tuya_ble_mesh.binary_sensor import (  # noqa: E402
    TuyaBLEMeshConnectivitySensor,
)
from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshDeviceState,
)


class TestConnectivityBinarySensor:
    """Test connectivity binary sensor entity."""

    def test_connectivity_sensor_creation(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test creating a connectivity binary sensor."""
        sensor = TuyaBLEMeshConnectivitySensor(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert sensor is not None
        assert sensor.unique_id is not None
        assert sensor.unique_id.endswith("_connectivity")

    def test_connectivity_sensor_device_class(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test sensor has CONNECTIVITY device class."""
        sensor = TuyaBLEMeshConnectivitySensor(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert sensor.device_class == BinarySensorDeviceClass.CONNECTIVITY

    def test_connectivity_sensor_entity_category(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test sensor is in DIAGNOSTIC category."""
        sensor = TuyaBLEMeshConnectivitySensor(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert sensor.entity_category == EntityCategory.DIAGNOSTIC

    def test_connectivity_sensor_is_on_when_available(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test is_on returns True when device is available."""
        mock_coordinator.state = TuyaBLEMeshDeviceState(available=True)
        sensor = TuyaBLEMeshConnectivitySensor(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert sensor.is_on is True

    def test_connectivity_sensor_is_off_when_unavailable(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test is_on returns False when device is unavailable."""
        mock_coordinator.state = TuyaBLEMeshDeviceState(available=False)
        sensor = TuyaBLEMeshConnectivitySensor(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert sensor.is_on is False

    def test_connectivity_sensor_always_available(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test sensor is always available (never hidden)."""
        # Test when device is available
        mock_coordinator.state = TuyaBLEMeshDeviceState(available=True)
        sensor = TuyaBLEMeshConnectivitySensor(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )
        assert sensor.available is True

        # Test when device is unavailable
        mock_coordinator.state = TuyaBLEMeshDeviceState(available=False)
        assert sensor.available is True

    def test_connectivity_sensor_unique_id_format(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test unique_id includes device address and _connectivity suffix."""
        mock_coordinator.device.address = "AA:BB:CC:DD:EE:FF"
        sensor = TuyaBLEMeshConnectivitySensor(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert sensor.unique_id == "AA:BB:CC:DD:EE:FF_connectivity"

    def test_connectivity_sensor_translation_key(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test sensor has correct translation key."""
        sensor = TuyaBLEMeshConnectivitySensor(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert sensor._attr_translation_key == "connectivity"
