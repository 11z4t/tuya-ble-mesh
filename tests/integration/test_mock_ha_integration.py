"""Home Assistant integration tests with mocked HA environment.

Simple integration tests that verify component behavior with mocked HA core.
Tests focus on interface contracts rather than internal implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))


class TestEntityBasicIntegration:
    """Test basic entity integration with HA."""

    @pytest.mark.asyncio
    async def test_light_entity_has_required_attributes(self) -> None:
        """Light entity should have required HA attributes."""
        from custom_components.tuya_ble_mesh.light import TuyaBLEMeshLight
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.mesh_id = 0x01

        mock_hass = MagicMock()
        mock_config_entry = MagicMock()
        mock_config_entry.entry_id = "test_entry"

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test_entry")
        light = TuyaBLEMeshLight(coord, mock_config_entry)

        # Required HA entity attributes
        assert hasattr(light, "unique_id")
        assert hasattr(light, "name")
        assert hasattr(light, "available")
        assert hasattr(light, "device_info")

    @pytest.mark.asyncio
    async def test_sensor_entity_has_required_attributes(self) -> None:
        """Sensor entity should have required HA attributes."""
        from custom_components.tuya_ble_mesh.sensor import TuyaBLEMeshSensor
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        mock_hass = MagicMock()
        mock_config_entry = MagicMock()
        mock_config_entry.entry_id = "test_entry"

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test_entry")
        sensor = TuyaBLEMeshSensor(coord, mock_config_entry, "rssi", "RSSI", "signal_strength")

        # Required HA sensor attributes
        assert hasattr(sensor, "unique_id")
        assert hasattr(sensor, "name")
        assert hasattr(sensor, "native_value")
        assert hasattr(sensor, "device_class")

    @pytest.mark.asyncio
    async def test_switch_entity_has_required_attributes(self) -> None:
        """Switch entity should have required HA attributes."""
        from custom_components.tuya_ble_mesh.switch import TuyaBLEMeshSwitch
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.mesh_id = 0x01

        mock_hass = MagicMock()
        mock_config_entry = MagicMock()
        mock_config_entry.entry_id = "test_entry"

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test_entry")
        switch = TuyaBLEMeshSwitch(coord, mock_config_entry)

        # Required HA switch attributes
        assert hasattr(switch, "unique_id")
        assert hasattr(switch, "name")
        assert hasattr(switch, "is_on")


class TestEntityServiceCalls:
    """Test entity service call handling."""

    @pytest.mark.asyncio
    async def test_light_turn_on_calls_device(self) -> None:
        """Light turn_on should delegate to device."""
        from custom_components.tuya_ble_mesh.light import TuyaBLEMeshLight
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.mesh_id = 0x01
        mock_device.turn_on = AsyncMock()

        mock_hass = MagicMock()
        mock_config_entry = MagicMock()
        mock_config_entry.entry_id = "test_entry"

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test_entry")
        light = TuyaBLEMeshLight(coord, mock_config_entry)

        await light.async_turn_on()

        mock_device.turn_on.assert_called_once()

    @pytest.mark.asyncio
    async def test_light_turn_off_calls_device(self) -> None:
        """Light turn_off should delegate to device."""
        from custom_components.tuya_ble_mesh.light import TuyaBLEMeshLight
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.mesh_id = 0x01
        mock_device.turn_off = AsyncMock()

        mock_hass = MagicMock()
        mock_config_entry = MagicMock()
        mock_config_entry.entry_id = "test_entry"

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test_entry")
        light = TuyaBLEMeshLight(coord, mock_config_entry)

        await light.async_turn_off()

        mock_device.turn_off.assert_called_once()

    @pytest.mark.asyncio
    async def test_switch_turn_on_calls_device(self) -> None:
        """Switch turn_on should delegate to device."""
        from custom_components.tuya_ble_mesh.switch import TuyaBLEMeshSwitch
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.mesh_id = 0x01
        mock_device.turn_on = AsyncMock()

        mock_hass = MagicMock()
        mock_config_entry = MagicMock()
        mock_config_entry.entry_id = "test_entry"

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test_entry")
        switch = TuyaBLEMeshSwitch(coord, mock_config_entry)

        await switch.async_turn_on()

        mock_device.turn_on.assert_called_once()


class TestCoordinatorBasics:
    """Test basic coordinator functionality."""

    @pytest.mark.asyncio
    async def test_coordinator_stores_device_reference(self) -> None:
        """Coordinator should store device reference."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        coord = TuyaBLEMeshCoordinator(mock_device)

        assert coord.device == mock_device

    @pytest.mark.asyncio
    async def test_coordinator_listener_registration(self) -> None:
        """Coordinator should support listener registration."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        coord = TuyaBLEMeshCoordinator(mock_device)

        callback_called = False

        def test_callback() -> None:
            nonlocal callback_called
            callback_called = True

        # Register listener
        remove_fn = coord.add_listener(test_callback)

        # Trigger notification
        coord._notify_listeners()

        # Verify callback was called
        assert callback_called

        # Test removal
        remove_fn()
        callback_called = False
        coord._notify_listeners()
        assert not callback_called

    @pytest.mark.asyncio
    async def test_coordinator_has_state_property(self) -> None:
        """Coordinator should expose state property."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Should have state property
        assert hasattr(coord, "state")
        state = coord.state
        assert state is not None


class TestConfigFlowBasics:
    """Test basic config flow functionality."""

    @pytest.mark.asyncio
    async def test_config_flow_has_version(self) -> None:
        """Config flow should have version number."""
        from custom_components.tuya_ble_mesh.config_flow import TuyaBLEMeshConfigFlow

        flow = TuyaBLEMeshConfigFlow()

        assert hasattr(flow, "VERSION")
        assert isinstance(flow.VERSION, int)
        assert flow.VERSION > 0

    @pytest.mark.asyncio
    async def test_config_flow_has_user_step(self) -> None:
        """Config flow should have user step."""
        from custom_components.tuya_ble_mesh.config_flow import TuyaBLEMeshConfigFlow

        flow = TuyaBLEMeshConfigFlow()

        assert hasattr(flow, "async_step_user")
        assert callable(flow.async_step_user)

    @pytest.mark.asyncio
    async def test_config_flow_has_bluetooth_step(self) -> None:
        """Config flow should have bluetooth discovery step."""
        from custom_components.tuya_ble_mesh.config_flow import TuyaBLEMeshConfigFlow

        flow = TuyaBLEMeshConfigFlow()

        assert hasattr(flow, "async_step_bluetooth")
        assert callable(flow.async_step_bluetooth)


class TestDiagnosticsBasics:
    """Test basic diagnostics functionality."""

    @pytest.mark.asyncio
    async def test_diagnostics_callable_exists(self) -> None:
        """Diagnostics module should have callable."""
        from custom_components.tuya_ble_mesh import diagnostics

        assert hasattr(diagnostics, "async_get_config_entry_diagnostics")
        assert callable(diagnostics.async_get_config_entry_diagnostics)

    @pytest.mark.asyncio
    async def test_diagnostics_returns_dict(self) -> None:
        """Diagnostics should return dictionary."""
        from custom_components.tuya_ble_mesh import diagnostics
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator
        from custom_components.tuya_ble_mesh.const import DOMAIN

        mock_hass = MagicMock()
        mock_config_entry = MagicMock()
        mock_config_entry.entry_id = "test_entry"
        mock_config_entry.data = {"address": "DC:23:4D:21:43:A5"}

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        coord = TuyaBLEMeshCoordinator(mock_device)
        mock_hass.data = {DOMAIN: {mock_config_entry.entry_id: coord}}

        result = await diagnostics.async_get_config_entry_diagnostics(
            mock_hass, mock_config_entry
        )

        assert isinstance(result, dict)


class TestPlatformSetup:
    """Test platform setup integration."""

    @pytest.mark.asyncio
    async def test_light_platform_setup_exists(self) -> None:
        """Light platform should have setup function."""
        from custom_components.tuya_ble_mesh import light

        assert hasattr(light, "async_setup_entry")
        assert callable(light.async_setup_entry)

    @pytest.mark.asyncio
    async def test_sensor_platform_setup_exists(self) -> None:
        """Sensor platform should have setup function."""
        from custom_components.tuya_ble_mesh import sensor

        assert hasattr(sensor, "async_setup_entry")
        assert callable(sensor.async_setup_entry)

    @pytest.mark.asyncio
    async def test_switch_platform_setup_exists(self) -> None:
        """Switch platform should have setup function."""
        from custom_components.tuya_ble_mesh import switch

        assert hasattr(switch, "async_setup_entry")
        assert callable(switch.async_setup_entry)
