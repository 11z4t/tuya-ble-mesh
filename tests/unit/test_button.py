"""Unit tests for Tuya BLE Mesh button entities."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from homeassistant.components.button import ButtonDeviceClass  # noqa: E402
from homeassistant.helpers.entity import EntityCategory  # noqa: E402

from custom_components.tuya_ble_mesh.button import (  # noqa: E402
    TuyaBLEMeshIdentifyButton,
    TuyaBLEMeshReconnectButton,
)


class TestIdentifyButton:
    """Test identify button entity."""

    def test_identify_button_creation(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test creating an identify button."""
        button = TuyaBLEMeshIdentifyButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert button is not None
        assert button.unique_id is not None
        assert button.unique_id.endswith("_identify")

    def test_identify_button_entity_category(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test button is in CONFIG category."""
        button = TuyaBLEMeshIdentifyButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert button.entity_category == EntityCategory.CONFIG

    def test_identify_button_icon(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test button has flash-alert icon."""
        button = TuyaBLEMeshIdentifyButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert button._attr_icon == "mdi:flash-alert"

    @pytest.mark.asyncio
    async def test_identify_button_press_flashes_device(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test pressing identify button flashes device LED."""
        mock_coordinator.device.send_power = AsyncMock()
        button = TuyaBLEMeshIdentifyButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        await button.async_press()

        # Should flash 3 times (3 on, 3 off = 6 calls)
        assert mock_coordinator.device.send_power.call_count == 6

    @pytest.mark.asyncio
    async def test_identify_button_press_without_send_power(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test identify button handles devices without send_power gracefully."""
        # Remove send_power method
        delattr(mock_coordinator.device, "send_power")
        button = TuyaBLEMeshIdentifyButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        # Should not raise, just log warning
        await button.async_press()

        assert "does not support identify" in caplog.text

    def test_identify_button_unique_id_format(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test unique_id includes device address and _identify suffix."""
        mock_coordinator.device.address = "AA:BB:CC:DD:EE:FF"
        button = TuyaBLEMeshIdentifyButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert button.unique_id == "AA:BB:CC:DD:EE:FF_identify"


class TestReconnectButton:
    """Test reconnect button entity."""

    def test_reconnect_button_creation(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test creating a reconnect button."""
        button = TuyaBLEMeshReconnectButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert button is not None
        assert button.unique_id is not None
        assert button.unique_id.endswith("_reconnect")

    def test_reconnect_button_device_class(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test button has RESTART device class."""
        button = TuyaBLEMeshReconnectButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert button.device_class == ButtonDeviceClass.RESTART

    def test_reconnect_button_entity_category(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test button is in CONFIG category."""
        button = TuyaBLEMeshReconnectButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert button.entity_category == EntityCategory.CONFIG

    def test_reconnect_button_always_available(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test button is always available (works even when offline)."""
        button = TuyaBLEMeshReconnectButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert button.available is True

    @pytest.mark.asyncio
    async def test_reconnect_button_press_disconnects_and_schedules(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test pressing reconnect button disconnects and schedules reconnect."""
        mock_coordinator.device.disconnect = AsyncMock()
        mock_coordinator.schedule_reconnect = MagicMock()
        button = TuyaBLEMeshReconnectButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        await button.async_press()

        mock_coordinator.device.disconnect.assert_called_once()
        mock_coordinator.schedule_reconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconnect_button_press_suppresses_oserror(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test reconnect button suppresses OSError from disconnect."""
        mock_coordinator.device.disconnect = AsyncMock(side_effect=OSError("Test error"))
        mock_coordinator.schedule_reconnect = MagicMock()
        button = TuyaBLEMeshReconnectButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        # Should not raise
        await button.async_press()

        # Still schedules reconnect
        mock_coordinator.schedule_reconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconnect_button_press_suppresses_connectionerror(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test reconnect button suppresses ConnectionError from disconnect."""
        mock_coordinator.device.disconnect = AsyncMock(side_effect=ConnectionError("Test error"))
        mock_coordinator.schedule_reconnect = MagicMock()
        button = TuyaBLEMeshReconnectButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        # Should not raise
        await button.async_press()

        # Still schedules reconnect
        mock_coordinator.schedule_reconnect.assert_called_once()

    def test_reconnect_button_unique_id_format(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test unique_id includes device address and _reconnect suffix."""
        mock_coordinator.device.address = "AA:BB:CC:DD:EE:FF"
        button = TuyaBLEMeshReconnectButton(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert button.unique_id == "AA:BB:CC:DD:EE:FF_reconnect"
