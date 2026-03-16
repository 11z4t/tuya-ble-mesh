"""Shared fixtures for integration tests.

PLAT-782: After PLAT-741 refactoring, validate_and_connect moved to config_flow_ble.
Integration tests need to mock it to avoid real BLE connections.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator

# Mock validate_and_connect where it's USED (in config_flow), not where it's defined
VALIDATE_AND_CONNECT_PATH = "custom_components.tuya_ble_mesh.config_flow.validate_and_connect"


@pytest.fixture(autouse=True)
def mock_ble_connection() -> Generator[AsyncMock, None, None]:
    """Auto-mock establish_connection from bleak_retry_connector.

    This prevents all tests from attempting real BLE connections.
    Returns a mock BleakClient that simulates successful connection.
    """
    with patch("bleak_retry_connector.establish_connection", new_callable=AsyncMock) as mock_conn:
        # Create a mock BleakClient that simulates successful connection
        mock_client = MagicMock()
        mock_client.address = "DC:23:4D:21:43:A5"
        mock_client.is_connected = True
        mock_client.disconnect = AsyncMock()
        mock_client.services = MagicMock()

        # Return the mock client
        mock_conn.return_value = mock_client
        yield mock_conn


@pytest.fixture(autouse=True)
def mock_ha_bluetooth() -> Generator[MagicMock, None, None]:
    """Auto-mock HA Bluetooth registry to return a valid BLE device.

    This prevents tests from needing real BlueZ/Bluetooth adapter.
    """
    with patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address"
    ) as mock_ble_device:
        # Return a mock BLEDevice that looks valid
        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.name = "Test Device"
        mock_ble_device.return_value = mock_device
        yield mock_ble_device


@pytest.fixture
def mock_coordinator_connection() -> Generator[AsyncMock, None, None]:
    """Mock coordinator async_initial_connect to avoid real device connection.

    Use this fixture explicitly in tests that call async_setup_entry
    but don't need to verify coordinator initialization details.
    """
    with patch(
        "custom_components.tuya_ble_mesh.coordinator.TuyaBLEMeshCoordinator.async_initial_connect",
        new_callable=AsyncMock,
    ) as mock_connect:
        # Default: succeed (return None)
        mock_connect.return_value = None
        yield mock_connect


@pytest.fixture(autouse=True)
def mock_validate_and_connect() -> Generator[AsyncMock, None, None]:
    """Auto-mock validate_and_connect for all integration tests.

    Returns detected device type and empty extra_data dict.
    Tests can override by patching the same path with different side_effect.
    """
    with patch(VALIDATE_AND_CONNECT_PATH, new_callable=AsyncMock) as mock_validate:
        # Default: succeed with detected device type matching input
        async def _mock_validate(
            hass,  # noqa: ARG001
            mac: str,  # noqa: ARG001
            device_type: str | None = None,
            mesh_name: str = "out_of_mesh",  # noqa: ARG001
            mesh_password: str = "123456",  # noqa: ARG001
        ) -> tuple[str, dict]:
            # Return the requested device type (or auto-detect as LIGHT if None)
            detected_type = device_type if device_type is not None else "light"
            return (detected_type, {})

        mock_validate.side_effect = _mock_validate
        yield mock_validate


@pytest.fixture(autouse=True)
def mock_test_bridge_with_session() -> Generator[AsyncMock, None, None]:
    """Auto-mock _test_bridge_with_session for bridge-based tests.

    PLAT-782: Function is in config_flow_validators, but imported and used
    in config_flow_sig and config_flow_telink modules.
    """
    # Patch where it's defined
    patch_path = "custom_components.tuya_ble_mesh.config_flow_validators._test_bridge_with_session"
    with patch(patch_path, new_callable=AsyncMock) as mock_bridge:
        # Default: succeed (no exception)
        mock_bridge.return_value = None
        yield mock_bridge
