"""Integration tests for async_setup_entry failure handling (PLAT-743).

Tests that ConfigEntryNotReady and ConfigEntryAuthFailed are raised correctly
when initial connection fails, giving HA Core visibility into integration health.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.tuya_ble_mesh.const import (
    CONF_MAC_ADDRESS,
    CONF_MESH_NAME,
    CONF_MESH_PASSWORD,
)


class TestSetupFailureHandling:
    """Test async_setup_entry raises ConfigEntryNotReady/AuthFailed on failure."""

    @pytest.mark.asyncio
    async def test_ble_connection_failure_raises_config_entry_not_ready(self) -> None:
        """BLE connection failure should raise ConfigEntryNotReady."""
        from custom_components.tuya_ble_mesh import async_setup_entry
        from homeassistant.exceptions import ConfigEntryNotReady

        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.async_add_import_executor_job = AsyncMock(return_value=None)

        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.title = "Test Device"
        mock_entry.data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_MESH_NAME: "out_of_mesh",
            CONF_MESH_PASSWORD: "123456",
        }

        # Mock device creation and coordinator with failing connect
        with (
            patch("custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.device.MeshDevice") as mock_device_cls,
            patch(
                "custom_components.tuya_ble_mesh.coordinator.TuyaBLEMeshCoordinator.async_initial_connect"
            ) as mock_connect,
        ):
            mock_device = MagicMock()
            mock_device.address = "DC:23:4D:21:43:A5"
            mock_device.firmware_version = "1.0.0"
            mock_device_cls.return_value = mock_device

            # Simulate BLE connection failure (OSError is typical for BLE failures)
            mock_connect.side_effect = OSError("Device not found")

            # Verify ConfigEntryNotReady is raised
            with pytest.raises(ConfigEntryNotReady) as exc_info:
                await async_setup_entry(mock_hass, mock_entry)

            # Verify translation keys are set
            assert exc_info.value.translation_domain == "tuya_ble_mesh"
            assert exc_info.value.translation_key == "device_connection_failed"

    @pytest.mark.asyncio
    async def test_mesh_auth_failure_raises_config_entry_auth_failed(self) -> None:
        """Mesh authentication failure should raise ConfigEntryAuthFailed."""
        from custom_components.tuya_ble_mesh import async_setup_entry
        from homeassistant.exceptions import ConfigEntryAuthFailed
        from custom_components.tuya_ble_mesh.error_classifier import ErrorClass

        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.async_add_import_executor_job = AsyncMock(return_value=None)

        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.title = "Test Device"
        mock_entry.data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_MESH_NAME: "out_of_mesh",
            CONF_MESH_PASSWORD: "123456",
        }

        # Mock device creation and coordinator with mesh auth failure
        with (
            patch("custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.device.MeshDevice") as mock_device_cls,
            patch(
                "custom_components.tuya_ble_mesh.coordinator.TuyaBLEMeshCoordinator.async_initial_connect"
            ) as mock_connect,
            patch(
                "custom_components.tuya_ble_mesh.coordinator.TuyaBLEMeshCoordinator._classify_error"
            ) as mock_classify,
        ):
            mock_device = MagicMock()
            mock_device.address = "DC:23:4D:21:43:A5"
            mock_device.firmware_version = "1.0.0"
            mock_device_cls.return_value = mock_device

            # Simulate mesh authentication failure
            mock_connect.side_effect = ValueError("Mesh authentication failed")
            mock_classify.return_value = ErrorClass.MESH_AUTH

            # Verify ConfigEntryAuthFailed is raised
            with pytest.raises(ConfigEntryAuthFailed) as exc_info:
                await async_setup_entry(mock_hass, mock_entry)

            # Verify translation keys are set
            assert exc_info.value.translation_domain == "tuya_ble_mesh"
            assert exc_info.value.translation_key == "mesh_auth_failed"

    @pytest.mark.asyncio
    async def test_timeout_raises_config_entry_not_ready(self) -> None:
        """Connection timeout should raise ConfigEntryNotReady."""
        from custom_components.tuya_ble_mesh import async_setup_entry
        from homeassistant.exceptions import ConfigEntryNotReady
        import asyncio

        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.async_add_import_executor_job = AsyncMock(return_value=None)

        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.title = "Test Device"
        mock_entry.data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_MESH_NAME: "out_of_mesh",
            CONF_MESH_PASSWORD: "123456",
        }

        # Mock device creation and coordinator with timeout
        with (
            patch("custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.device.MeshDevice") as mock_device_cls,
            patch(
                "custom_components.tuya_ble_mesh.coordinator.TuyaBLEMeshCoordinator.async_initial_connect"
            ) as mock_connect,
        ):
            mock_device = MagicMock()
            mock_device.address = "DC:23:4D:21:43:A5"
            mock_device.firmware_version = "1.0.0"
            mock_device_cls.return_value = mock_device

            # Simulate connection timeout
            mock_connect.side_effect = asyncio.TimeoutError("Connection timed out")

            # Verify ConfigEntryNotReady is raised
            with pytest.raises(ConfigEntryNotReady) as exc_info:
                await async_setup_entry(mock_hass, mock_entry)

            # Verify translation keys are set
            assert exc_info.value.translation_domain == "tuya_ble_mesh"
            assert exc_info.value.translation_key == "device_connection_failed"

    @pytest.mark.asyncio
    async def test_successful_connection_returns_true(self) -> None:
        """Successful connection should return True and not raise exceptions."""
        from custom_components.tuya_ble_mesh import async_setup_entry

        mock_hass = MagicMock()
        mock_hass.config_entries = MagicMock()
        mock_hass.config_entries.async_forward_entry_setups = AsyncMock()
        mock_hass.services = MagicMock()
        mock_hass.services.has_service = MagicMock(return_value=False)
        mock_hass.services.async_register = MagicMock()
        mock_hass.async_add_import_executor_job = AsyncMock(return_value=None)

        mock_entry = MagicMock()
        mock_entry.entry_id = "test_entry"
        mock_entry.title = "Test Device"
        mock_entry.data = {
            CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
            CONF_MESH_NAME: "out_of_mesh",
            CONF_MESH_PASSWORD: "123456",
        }
        mock_entry.async_on_unload = MagicMock(return_value=None)

        # Mock device creation and successful connection
        with (
            patch("custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.device.MeshDevice") as mock_device_cls,
            patch(
                "custom_components.tuya_ble_mesh.coordinator.TuyaBLEMeshCoordinator.async_initial_connect"
            ) as mock_connect,
        ):
            mock_device = MagicMock()
            mock_device.address = "DC:23:4D:21:43:A5"
            mock_device.firmware_version = "1.2.3"
            mock_device_cls.return_value = mock_device

            # Simulate successful connection
            mock_connect.return_value = None

            # Verify setup succeeds
            result = await async_setup_entry(mock_hass, mock_entry)
            assert result is True

            # Verify coordinator was connected
            mock_connect.assert_called_once()

            # Verify runtime_data was set
            assert hasattr(mock_entry, "runtime_data")
            assert mock_entry.runtime_data is not None

    @pytest.mark.asyncio
    async def test_device_offline_logs_warning(self) -> None:
        """Device offline should log warning and raise ConfigEntryNotReady."""
        from custom_components.tuya_ble_mesh import async_setup_entry
        from homeassistant.exceptions import ConfigEntryNotReady
        import logging

        # Capture log output
        logger = logging.getLogger("custom_components.tuya_ble_mesh")
        with patch.object(logger, "warning") as mock_warning:
            mock_hass = MagicMock()
            mock_hass.config_entries = MagicMock()
            mock_hass.async_add_import_executor_job = AsyncMock(return_value=None)

            mock_entry = MagicMock()
            mock_entry.entry_id = "test_entry"
            mock_entry.title = "Test Device"
            mock_entry.data = {
                CONF_MAC_ADDRESS: "DC:23:4D:21:43:A5",
                CONF_MESH_NAME: "out_of_mesh",
                CONF_MESH_PASSWORD: "123456",
            }

            # Mock device creation and offline device
            with (
                patch("custom_components.tuya_ble_mesh.lib.tuya_ble_mesh.device.MeshDevice") as mock_device_cls,
                patch(
                    "custom_components.tuya_ble_mesh.coordinator.TuyaBLEMeshCoordinator.async_initial_connect"
                ) as mock_connect,
            ):
                mock_device = MagicMock()
                mock_device.address = "DC:23:4D:21:43:A5"
                mock_device.firmware_version = "1.0.0"
                mock_device_cls.return_value = mock_device

                # Simulate device offline
                mock_connect.side_effect = OSError("Device not reachable")

                # Verify ConfigEntryNotReady is raised
                with pytest.raises(ConfigEntryNotReady):
                    await async_setup_entry(mock_hass, mock_entry)

                # Verify warning was logged
                mock_warning.assert_called()
                assert "Initial connection failed" in str(mock_warning.call_args)
