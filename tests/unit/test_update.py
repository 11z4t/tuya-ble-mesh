"""Unit tests for Tuya BLE Mesh update (firmware) entities."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from homeassistant.components.update import UpdateDeviceClass  # noqa: E402

from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshDeviceState,
)
from custom_components.tuya_ble_mesh.update import (  # noqa: E402
    TuyaBLEMeshFirmwareUpdateEntity,
)


class TestFirmwareUpdateEntity:
    """Test firmware update entity."""

    def test_update_entity_creation(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test creating a firmware update entity."""
        entity = TuyaBLEMeshFirmwareUpdateEntity(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert entity is not None
        assert entity.unique_id is not None
        assert entity.unique_id.endswith("_firmware")

    def test_update_entity_device_class(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test entity has FIRMWARE device class."""
        entity = TuyaBLEMeshFirmwareUpdateEntity(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert entity.device_class == UpdateDeviceClass.FIRMWARE

    def test_update_entity_translation_key(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test entity has correct translation key."""
        entity = TuyaBLEMeshFirmwareUpdateEntity(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert entity._attr_translation_key == "firmware_update"

    def test_installed_version_from_state(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test installed_version returns firmware from coordinator state."""
        mock_coordinator.state = TuyaBLEMeshDeviceState(firmware_version="1.6")
        entity = TuyaBLEMeshFirmwareUpdateEntity(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert entity.installed_version == "1.6"

    def test_installed_version_none(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test installed_version returns None when firmware unknown."""
        mock_coordinator.state = TuyaBLEMeshDeviceState(firmware_version=None)
        entity = TuyaBLEMeshFirmwareUpdateEntity(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert entity.installed_version is None

    def test_latest_version_matches_installed(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test latest_version returns same as installed (no update server)."""
        mock_coordinator.state = TuyaBLEMeshDeviceState(firmware_version="2.1")
        entity = TuyaBLEMeshFirmwareUpdateEntity(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        # Both should be the same (no update available info)
        assert entity.latest_version == entity.installed_version
        assert entity.latest_version == "2.1"

    def test_latest_version_none_when_unknown(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test latest_version returns None when firmware unknown."""
        mock_coordinator.state = TuyaBLEMeshDeviceState(firmware_version=None)
        entity = TuyaBLEMeshFirmwareUpdateEntity(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert entity.latest_version is None

    def test_release_notes_always_none(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test release_notes is always None (no release info available)."""
        entity = TuyaBLEMeshFirmwareUpdateEntity(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert entity.release_notes is None

    @pytest.mark.parametrize(
        "firmware_version",
        ["1.0", "1.6", "2.1", "3.14.159", None],
    )
    def test_update_entity_various_versions(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
        firmware_version: str | None,
    ) -> None:
        """Test entity handles various firmware version strings."""
        mock_coordinator.state = TuyaBLEMeshDeviceState(firmware_version=firmware_version)
        entity = TuyaBLEMeshFirmwareUpdateEntity(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert entity.installed_version == firmware_version
        assert entity.latest_version == firmware_version

    def test_update_entity_unique_id_format(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test unique_id uses entry_id with _firmware suffix."""
        mock_config_entry.entry_id = "test_entry_123"
        entity = TuyaBLEMeshFirmwareUpdateEntity(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert entity.unique_id == "test_entry_123_firmware"
