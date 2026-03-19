"""Shared fixtures for unit tests.

Provides a default mock for ha_bluetooth.async_ble_device_from_address so that
config_flow tests that call async_step_confirm / async_step_sig_plug work without
a real HA BluetoothManager.  Tests that specifically need the function to return
None (e.g. stale-device tests) override with their own ``patch`` context manager.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_ble_device_from_address():
    """Patch async_ble_device_from_address to return a non-None mock by default.

    Config flow steps (async_step_confirm, async_step_sig_plug) check device
    availability via this function.  Without the patch the HA BluetoothManager
    singleton is not initialised in unit tests and raises RuntimeError.
    """
    mock_device = MagicMock()
    with patch(
        "homeassistant.components.bluetooth.async_ble_device_from_address",
        return_value=mock_device,
    ) as mock_fn:
        yield mock_fn


@pytest.fixture(autouse=True)
def mock_validate_and_connect():
    """Patch validate_and_connect to avoid real BLE connections in tests.

    PLAT-782: Tests were failing because validate_and_connect from config_flow_ble
    was attempting real bleak connections, which fail without bluez daemon.
    This fixture mocks the function globally for all unit tests.
    """

    async def _mock_validate(
        hass,
        mac: str,
        device_type: str | None = None,
        mesh_name: str = "out_of_mesh",
        mesh_password: str = "123456",
    ) -> tuple[str, dict]:
        """Mock validation that always succeeds."""
        from custom_components.tuya_ble_mesh.const import DEVICE_TYPE_LIGHT

        detected = device_type if device_type else DEVICE_TYPE_LIGHT
        return (detected, {})

    # Patch both the original location and where it's imported to
    with (
        patch(
            "custom_components.tuya_ble_mesh.config_flow_ble.validate_and_connect",
            new=AsyncMock(side_effect=_mock_validate),
        ),
        patch(
            "custom_components.tuya_ble_mesh.config_flow.validate_and_connect",
            new=AsyncMock(side_effect=_mock_validate),
        ),
        patch(
            "custom_components.tuya_ble_mesh.config_flow_discovery.validate_and_connect",
            new=AsyncMock(side_effect=_mock_validate),
        ),
    ):
        yield
