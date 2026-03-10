"""Shared fixtures for unit tests.

Provides a default mock for ha_bluetooth.async_ble_device_from_address so that
config_flow tests that call async_step_confirm / async_step_sig_plug work without
a real HA BluetoothManager.  Tests that specifically need the function to return
None (e.g. stale-device tests) override with their own ``patch`` context manager.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

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
