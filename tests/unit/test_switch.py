"""Unit tests for Tuya BLE Mesh switch entities."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from homeassistant.components.switch import SwitchDeviceClass  # noqa: E402

from custom_components.tuya_ble_mesh.const import (  # noqa: E402
    DEVICE_TYPE_PLUG,
    DEVICE_TYPE_SIG_BRIDGE_PLUG,
    DEVICE_TYPE_SIG_PLUG,
)
from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshDeviceState,
)
from custom_components.tuya_ble_mesh.switch import TuyaBLEMeshSwitch  # noqa: E402


class TestSwitchEntity:
    """Test TuyaBLEMeshSwitch entity creation and basic properties."""

    def test_switch_entity_creation(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test creating a switch entity for a plug."""
        mock_config_entry.data["device_type"] = DEVICE_TYPE_PLUG
        switch = TuyaBLEMeshSwitch(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert switch is not None
        assert switch.unique_id is not None
        assert switch.unique_id.endswith("_switch")
        assert switch.coordinator == mock_coordinator

    def test_switch_device_class(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test switch has OUTLET device class."""
        switch = TuyaBLEMeshSwitch(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert switch.device_class == SwitchDeviceClass.OUTLET

    def test_switch_is_on_true(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test is_on property returns True when switch is on."""
        mock_coordinator.state = TuyaBLEMeshDeviceState(is_on=True)
        switch = TuyaBLEMeshSwitch(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert switch.is_on is True

    def test_switch_is_on_false(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test is_on property returns False when switch is off."""
        mock_coordinator.state = TuyaBLEMeshDeviceState(is_on=False)
        switch = TuyaBLEMeshSwitch(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert switch.is_on is False

    @pytest.mark.asyncio
    async def test_switch_turn_on(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test turn_on command sends power(True) via coordinator."""
        mock_coordinator.send_command_with_retry = AsyncMock()
        switch = TuyaBLEMeshSwitch(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        await switch.async_turn_on()

        mock_coordinator.send_command_with_retry.assert_called_once()
        # Verify the command description mentions power(True)
        call_args = mock_coordinator.send_command_with_retry.call_args
        assert "description" in call_args.kwargs
        assert "True" in call_args.kwargs["description"]

    @pytest.mark.asyncio
    async def test_switch_turn_off(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test turn_off command sends power(False) via coordinator."""
        mock_coordinator.send_command_with_retry = AsyncMock()
        switch = TuyaBLEMeshSwitch(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        await switch.async_turn_off()

        mock_coordinator.send_command_with_retry.assert_called_once()
        # Verify the command description mentions power(False)
        call_args = mock_coordinator.send_command_with_retry.call_args
        assert "description" in call_args.kwargs
        assert "False" in call_args.kwargs["description"]

    @pytest.mark.parametrize(
        "device_type",
        [DEVICE_TYPE_PLUG, DEVICE_TYPE_SIG_PLUG, DEVICE_TYPE_SIG_BRIDGE_PLUG],
    )
    def test_switch_parametrized_plug_types(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
        device_type: str,
    ) -> None:
        """Test switch entity works with all plug device types."""
        mock_config_entry.data["device_type"] = device_type
        switch = TuyaBLEMeshSwitch(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert switch is not None
        assert switch.unique_id is not None
        assert switch.device_class == SwitchDeviceClass.OUTLET

    @pytest.mark.asyncio
    async def test_switch_turn_on_with_kwargs(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test turn_on accepts and ignores kwargs."""
        mock_coordinator.send_command_with_retry = AsyncMock()
        switch = TuyaBLEMeshSwitch(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        # Should not raise even with extra kwargs
        await switch.async_turn_on(extra_param="ignored")

        mock_coordinator.send_command_with_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_switch_turn_off_with_kwargs(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test turn_off accepts and ignores kwargs."""
        mock_coordinator.send_command_with_retry = AsyncMock()
        switch = TuyaBLEMeshSwitch(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        # Should not raise even with extra kwargs
        await switch.async_turn_off(extra_param="ignored")

        mock_coordinator.send_command_with_retry.assert_called_once()

    def test_switch_unique_id_format(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test unique_id includes device address and _switch suffix."""
        mock_coordinator.device.address = "AA:BB:CC:DD:EE:FF"
        switch = TuyaBLEMeshSwitch(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert switch.unique_id == "AA:BB:CC:DD:EE:FF_switch"

    def test_switch_should_not_poll(
        self,
        mock_coordinator: MagicMock,
        mock_config_entry: MagicMock,
    ) -> None:
        """Test switch has should_poll=False (uses coordinator updates)."""
        switch = TuyaBLEMeshSwitch(
            mock_coordinator,
            mock_config_entry.entry_id,
            device_info=None,
        )

        assert switch.should_poll is False
