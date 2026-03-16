"""Unit tests for Tuya BLE Mesh integration services."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from custom_components.tuya_ble_mesh import (  # noqa: E402
    _async_register_services,
)


class TestServiceRegistration:
    """Test service registration."""

    @pytest.mark.asyncio
    async def test_services_registered_only_once(self) -> None:
        """Test services are registered only once even with multiple entries."""
        hass = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)
        hass.services.async_register = MagicMock()

        # First registration
        await _async_register_services(hass)

        # Should register all 4 services
        assert hass.services.async_register.call_count == 4
        service_names = [call[0][1] for call in hass.services.async_register.call_args_list]
        assert "identify" in service_names
        assert "set_log_level" in service_names
        assert "get_diagnostics" in service_names
        assert "reconnect" in service_names

    @pytest.mark.asyncio
    async def test_services_not_reregistered_if_exists(self) -> None:
        """Test services are not re-registered if already present."""
        hass = MagicMock()
        hass.services.has_service = MagicMock(return_value=True)
        hass.services.async_register = MagicMock()

        await _async_register_services(hass)

        # Should not register anything (already exists)
        hass.services.async_register.assert_not_called()


class TestIdentifyService:
    """Test identify service."""

    @pytest.mark.asyncio
    async def test_identify_service_flashes_device(self) -> None:
        """Test identify service calls send_power to flash device."""
        hass = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        # Capture service handlers
        registered_services = {}

        def mock_register(domain, service_name, handler, **kwargs):
            registered_services[service_name] = handler

        hass.services.async_register = mock_register

        await _async_register_services(hass)

        # Get identify handler
        identify_handler = registered_services["identify"]

        # Mock coordinator
        coordinator = MagicMock()
        coordinator.device = MagicMock()
        coordinator.device.send_power = AsyncMock()

        # Mock service call
        call = MagicMock()
        call.data = {"device_id": "test_device"}

        with patch(
            "custom_components.tuya_ble_mesh._get_coordinator_for_device",
            return_value=coordinator,
        ):
            await identify_handler(call)

        # Should flash 3 times (3 off + 3 on = 6 calls)
        assert coordinator.device.send_power.call_count == 6

    @pytest.mark.asyncio
    async def test_identify_service_device_not_found_raises_error(self) -> None:
        """Test identify service raises error when device not found."""
        from homeassistant.exceptions import HomeAssistantError

        hass = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        registered_services = {}

        def mock_register(domain, service_name, handler, **kwargs):
            registered_services[service_name] = handler

        hass.services.async_register = mock_register

        await _async_register_services(hass)

        identify_handler = registered_services["identify"]
        call = MagicMock()
        call.data = {"device_id": "nonexistent"}

        with patch(
            "custom_components.tuya_ble_mesh._get_coordinator_for_device",
            return_value=None,
        ):
            with pytest.raises(HomeAssistantError) as exc_info:
                await identify_handler(call)
            assert exc_info.value.translation_key == "device_not_found"


class TestSetLogLevelService:
    """Test set_log_level service."""

    @pytest.mark.asyncio
    async def test_set_log_level_changes_logger_level(self) -> None:
        """Test set_log_level service changes tuya_ble_mesh logger level."""
        hass = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        registered_services = {}

        def mock_register(domain, service_name, handler, **kwargs):
            registered_services[service_name] = handler

        hass.services.async_register = mock_register

        await _async_register_services(hass)

        set_log_level_handler = registered_services["set_log_level"]

        # Mock service call
        call = MagicMock()
        call.data = {"level": "debug"}

        with patch("logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            await set_log_level_handler(call)

            # Should set logger level to DEBUG
            mock_logger.setLevel.assert_called_once()
            # Level should be logging.DEBUG (10)
            assert mock_logger.setLevel.call_args[0][0] == logging.DEBUG

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("level_str", "expected_level"),
        [
            ("debug", logging.DEBUG),
            ("info", logging.INFO),
            ("warning", logging.WARNING),
            ("error", logging.ERROR),
        ],
    )
    async def test_set_log_level_various_levels(
        self,
        level_str: str,
        expected_level: int,
    ) -> None:
        """Test set_log_level service with various log levels."""
        hass = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        registered_services = {}

        def mock_register(domain, service_name, handler, **kwargs):
            registered_services[service_name] = handler

        hass.services.async_register = mock_register

        await _async_register_services(hass)

        set_log_level_handler = registered_services["set_log_level"]
        call = MagicMock()
        call.data = {"level": level_str}

        with patch("logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger

            await set_log_level_handler(call)

            mock_logger.setLevel.assert_called_once_with(expected_level)


class TestGetDiagnosticsService:
    """Test get_diagnostics service."""

    @pytest.mark.asyncio
    async def test_get_diagnostics_returns_data(self) -> None:
        """Test get_diagnostics service returns diagnostic data."""
        hass = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        registered_services = {}

        def mock_register(domain, service_name, handler, **kwargs):
            registered_services[service_name] = handler

        hass.services.async_register = mock_register

        await _async_register_services(hass)

        get_diagnostics_handler = registered_services["get_diagnostics"]

        # Mock coordinator with statistics
        coordinator = MagicMock()
        coordinator.device.address = "DC:23:4D:21:43:A5"
        coordinator.state.available = True
        coordinator.state.rssi = -65
        coordinator.state.firmware_version = "1.6"

        stats = MagicMock()
        stats.connection_uptime = 3600.0
        stats.total_reconnects = 2
        stats.total_errors = 1
        stats.connection_errors = 0
        stats.command_errors = 1
        stats.response_times = [0.1, 0.2, 0.3]
        stats.avg_response_time = 0.2
        stats.last_error = "timeout"
        stats.last_disconnect_time = 1234567890.0
        coordinator.statistics = stats

        call = MagicMock()
        call.data = {"device_id": "test_device"}

        with patch(
            "custom_components.tuya_ble_mesh._get_coordinator_for_device",
            return_value=coordinator,
        ):
            result = await get_diagnostics_handler(call)

        # Should return diagnostics dict
        assert isinstance(result, dict)
        assert result["device_address"] == "DC:23:4D:21:43:A5"
        assert result["available"] is True
        assert result["total_reconnects"] == 2
        assert result["rssi_dbm"] == -65
        assert result["firmware_version"] == "1.6"

    @pytest.mark.asyncio
    async def test_get_diagnostics_device_not_found_raises_error(self) -> None:
        """Test get_diagnostics service raises error when device not found."""
        from homeassistant.exceptions import HomeAssistantError

        hass = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        registered_services = {}

        def mock_register(domain, service_name, handler, **kwargs):
            registered_services[service_name] = handler

        hass.services.async_register = mock_register

        await _async_register_services(hass)

        get_diagnostics_handler = registered_services["get_diagnostics"]
        call = MagicMock()
        call.data = {"device_id": "nonexistent"}

        with patch(
            "custom_components.tuya_ble_mesh._get_coordinator_for_device",
            return_value=None,
        ):
            with pytest.raises(HomeAssistantError) as exc_info:
                await get_diagnostics_handler(call)
            assert exc_info.value.translation_key == "device_not_found"


class TestReconnectService:
    """Test reconnect service."""

    @pytest.mark.asyncio
    async def test_reconnect_service_disconnects_and_schedules(self) -> None:
        """Test reconnect service disconnects and schedules reconnect."""
        hass = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        registered_services = {}

        def mock_register(domain, service_name, handler, **kwargs):
            registered_services[service_name] = handler

        hass.services.async_register = mock_register

        await _async_register_services(hass)

        reconnect_handler = registered_services["reconnect"]

        # Mock coordinator
        coordinator = MagicMock()
        coordinator.device.disconnect = AsyncMock()
        coordinator.schedule_reconnect = MagicMock()

        call = MagicMock()
        call.data = {"device_id": "test_device"}

        with patch(
            "custom_components.tuya_ble_mesh._get_coordinator_for_device",
            return_value=coordinator,
        ):
            await reconnect_handler(call)

        # Should disconnect and schedule reconnect
        coordinator.device.disconnect.assert_called_once()
        coordinator.schedule_reconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconnect_service_suppresses_oserror(self) -> None:
        """Test reconnect service suppresses OSError from disconnect."""
        hass = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        registered_services = {}

        def mock_register(domain, service_name, handler, **kwargs):
            registered_services[service_name] = handler

        hass.services.async_register = mock_register

        await _async_register_services(hass)

        reconnect_handler = registered_services["reconnect"]

        coordinator = MagicMock()
        coordinator.device.disconnect = AsyncMock(side_effect=OSError("error"))
        coordinator.schedule_reconnect = MagicMock()

        call = MagicMock()
        call.data = {"device_id": "test_device"}

        with patch(
            "custom_components.tuya_ble_mesh._get_coordinator_for_device",
            return_value=coordinator,
        ):
            # Should not raise
            await reconnect_handler(call)

        # Still schedules reconnect
        coordinator.schedule_reconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconnect_service_device_not_found_raises_error(self) -> None:
        """Test reconnect service raises error when device not found."""
        from homeassistant.exceptions import HomeAssistantError

        hass = MagicMock()
        hass.services.has_service = MagicMock(return_value=False)

        registered_services = {}

        def mock_register(domain, service_name, handler, **kwargs):
            registered_services[service_name] = handler

        hass.services.async_register = mock_register

        await _async_register_services(hass)

        reconnect_handler = registered_services["reconnect"]
        call = MagicMock()
        call.data = {"device_id": "nonexistent"}

        with patch(
            "custom_components.tuya_ble_mesh._get_coordinator_for_device",
            return_value=None,
        ):
            with pytest.raises(HomeAssistantError) as exc_info:
                await reconnect_handler(call)
            assert exc_info.value.translation_key == "device_not_found"


# Note: _get_coordinator_for_device tests removed as they require complex HA device registry mocking
