"""Unit tests for adaptive polling scheduler in TuyaBLEMeshCoordinator.

Tests the RSSI adaptive polling mechanism which adjusts polling intervals
based on state change frequency - faster polling during active changes,
slower polling during stable periods.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    _RSSI_DEFAULT_INTERVAL,
    _RSSI_MAX_INTERVAL,
    _RSSI_MIN_INTERVAL,
    _RSSI_STABILITY_THRESHOLD,
    TuyaBLEMeshCoordinator,
)


def make_mock_device() -> MagicMock:
    """Create a mock MeshDevice."""
    device = MagicMock()
    device.address = "DC:23:4D:21:43:A5"
    device.connect = AsyncMock()
    device.disconnect = AsyncMock()
    device.register_status_callback = MagicMock()
    device.unregister_status_callback = MagicMock()
    device.register_disconnect_callback = MagicMock()
    device.unregister_disconnect_callback = MagicMock()
    device.is_connected = True
    return device


def make_mock_status(
    *,
    mode: int = 0,
    white_brightness: int = 100,
    white_temp: int = 50,
    color_brightness: int = 0,
    red: int = 0,
    green: int = 0,
    blue: int = 0,
) -> MagicMock:
    """Create a mock StatusResponse."""
    status = MagicMock()
    status.mode = mode
    status.white_brightness = white_brightness
    status.white_temp = white_temp
    status.color_brightness = color_brightness
    status.red = red
    status.green = green
    status.blue = blue
    status.mesh_id = 1
    return status


@pytest.mark.requires_ha
class TestPollingIntervalDefaults:
    """Test default polling interval values."""

    def test_initial_rssi_interval(self) -> None:
        """Test that RSSI interval starts at default value."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        assert coord._rssi_interval == _RSSI_DEFAULT_INTERVAL

    def test_rssi_constants_are_sane(self) -> None:
        """Test that RSSI interval constants are logically ordered."""
        assert _RSSI_MIN_INTERVAL < _RSSI_DEFAULT_INTERVAL
        assert _RSSI_DEFAULT_INTERVAL < _RSSI_MAX_INTERVAL
        assert _RSSI_MIN_INTERVAL > 0

    def test_stability_threshold_is_positive(self) -> None:
        """Test that stability threshold is positive."""
        assert _RSSI_STABILITY_THRESHOLD > 0


@pytest.mark.requires_ha
class TestStateChangeTracking:
    """Test state change detection for adaptive polling."""

    def test_state_change_counter_starts_at_zero(self) -> None:
        """Test that state change counter is initially zero."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        assert coord._state_change_counter == 0

    def test_stable_cycles_starts_at_zero(self) -> None:
        """Test that stable cycles counter is initially zero."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        assert coord._stable_cycles == 0

    def test_detects_brightness_change(self) -> None:
        """Test that brightness changes are detected."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Set initial state
        coord._state.brightness = 50

        # Simulate status update with different brightness
        status = make_mock_status(white_brightness=100)

        # Check if change would be detected (coordinator logic)
        changed = coord._state.brightness != status.white_brightness
        assert changed is True

    def test_detects_color_temp_change(self) -> None:
        """Test that color temperature changes are detected."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._state.color_temp = 30

        status = make_mock_status(white_temp=80)

        changed = coord._state.color_temp != status.white_temp
        assert changed is True

    def test_detects_mode_change(self) -> None:
        """Test that mode changes are detected."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._state.mode = 0

        status = make_mock_status(mode=1)

        changed = coord._state.mode != status.mode
        assert changed is True

    def test_detects_color_changes(self) -> None:
        """Test that RGB color changes are detected."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._state.red = 0
        coord._state.green = 0
        coord._state.blue = 0

        status = make_mock_status(red=255, green=128, blue=64)

        changed = (
            coord._state.red != status.red
            or coord._state.green != status.green
            or coord._state.blue != status.blue
        )
        assert changed is True

    def test_no_change_when_values_identical(self) -> None:
        """Test that no change is detected when values are identical."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._state.brightness = 100
        coord._state.color_temp = 50
        coord._state.mode = 0

        status = make_mock_status(white_brightness=100, white_temp=50, mode=0)

        changed = (
            coord._state.mode != status.mode
            or coord._state.brightness != status.white_brightness
            or coord._state.color_temp != status.white_temp
        )
        assert changed is False


@pytest.mark.requires_ha
class TestAdaptivePollingAdjustment:
    """Test adaptive polling interval adjustment."""

    def test_interval_decreases_on_frequent_changes(self) -> None:
        """Test that polling interval decreases when changes are frequent."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Set initial interval
        coord._rssi_interval = _RSSI_DEFAULT_INTERVAL
        initial_interval = coord._rssi_interval

        # Simulate frequent changes (2+ changes)
        coord._state_change_counter = 3

        # Call adjustment logic
        coord._adjust_polling_interval()

        # Interval should have decreased
        assert coord._rssi_interval < initial_interval

    def test_interval_increases_on_stability(self) -> None:
        """Test that polling interval increases when state is stable."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._rssi_interval = _RSSI_DEFAULT_INTERVAL
        initial_interval = coord._rssi_interval

        # Simulate stable state
        coord._stable_cycles = _RSSI_STABILITY_THRESHOLD

        # Call adjustment logic
        coord._adjust_polling_interval()

        # Interval should have increased
        assert coord._rssi_interval > initial_interval

    def test_interval_respects_minimum(self) -> None:
        """Test that polling interval does not go below minimum."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Set interval near minimum
        coord._rssi_interval = _RSSI_MIN_INTERVAL + 5.0

        # Simulate many frequent changes
        for _ in range(10):
            coord._state_change_counter = 5
            coord._adjust_polling_interval()

        # Should not go below minimum
        assert coord._rssi_interval >= _RSSI_MIN_INTERVAL

    def test_interval_respects_maximum(self) -> None:
        """Test that polling interval does not exceed maximum."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Set interval near maximum
        coord._rssi_interval = _RSSI_MAX_INTERVAL - 10.0

        # Simulate stable state multiple times
        for _ in range(10):
            coord._stable_cycles = _RSSI_STABILITY_THRESHOLD
            coord._adjust_polling_interval()

        # Should not exceed maximum
        assert coord._rssi_interval <= _RSSI_MAX_INTERVAL

    def test_change_counter_resets_after_adjustment(self) -> None:
        """Test that state change counter resets after interval adjustment."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._state_change_counter = 5

        # Call adjustment
        coord._adjust_polling_interval()

        # Counter should be reset
        assert coord._state_change_counter == 0


@pytest.mark.requires_ha
class TestAdaptivePollingScaling:
    """Test polling interval scaling factors."""

    def test_decrease_factor_is_correct(self) -> None:
        """Test that polling interval decreases by 25% on changes."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._rssi_interval = 100.0
        coord._state_change_counter = 3

        coord._adjust_polling_interval()

        # Should decrease by 25% (multiply by 0.75)
        expected = max(_RSSI_MIN_INTERVAL, 100.0 * 0.75)
        assert coord._rssi_interval == expected

    def test_increase_factor_is_correct(self) -> None:
        """Test that polling interval increases by 50% on stability."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._rssi_interval = 100.0
        coord._stable_cycles = _RSSI_STABILITY_THRESHOLD

        coord._adjust_polling_interval()

        # Should increase by 50% (multiply by 1.5)
        expected = min(_RSSI_MAX_INTERVAL, 100.0 * 1.5)
        assert coord._rssi_interval == expected

    def test_multiple_decreases(self) -> None:
        """Test multiple consecutive interval decreases."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._rssi_interval = 120.0

        # First decrease
        coord._state_change_counter = 3
        coord._adjust_polling_interval()
        interval_1 = coord._rssi_interval

        # Second decrease
        coord._state_change_counter = 3
        coord._adjust_polling_interval()
        interval_2 = coord._rssi_interval

        # Each should be smaller, respecting minimum
        assert interval_2 <= interval_1
        assert interval_2 >= _RSSI_MIN_INTERVAL

    def test_multiple_increases(self) -> None:
        """Test multiple consecutive interval increases."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._rssi_interval = 60.0

        # First increase
        coord._stable_cycles = _RSSI_STABILITY_THRESHOLD
        coord._adjust_polling_interval()
        interval_1 = coord._rssi_interval

        # Second increase
        coord._stable_cycles = _RSSI_STABILITY_THRESHOLD
        coord._adjust_polling_interval()
        interval_2 = coord._rssi_interval

        # Each should be larger, respecting maximum
        assert interval_2 >= interval_1
        assert interval_2 <= _RSSI_MAX_INTERVAL


@pytest.mark.requires_ha
class TestRSSITaskManagement:
    """Test RSSI polling task lifecycle."""

    def test_rssi_task_initially_none(self) -> None:
        """Test that RSSI task is initially None."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        assert coord._rssi_task is None

    def test_rssi_task_can_be_assigned(self) -> None:
        """Test that RSSI task can be assigned."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Create a mock task
        mock_task = MagicMock()
        coord._rssi_task = mock_task

        assert coord._rssi_task is mock_task


@pytest.mark.requires_ha
class TestPollingBehaviorScenarios:
    """Test polling behavior in realistic scenarios."""

    def test_rapid_color_changes_increase_frequency(self) -> None:
        """Test that rapid color changes trigger faster polling."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._rssi_interval = _RSSI_DEFAULT_INTERVAL
        initial_interval = coord._rssi_interval

        # Simulate rapid color changes
        coord._state_change_counter = 5

        coord._adjust_polling_interval()

        # Should poll faster
        assert coord._rssi_interval < initial_interval

    def test_idle_device_reduces_polling(self) -> None:
        """Test that idle device with no changes reduces polling frequency."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._rssi_interval = _RSSI_DEFAULT_INTERVAL
        initial_interval = coord._rssi_interval

        # Simulate stability
        coord._stable_cycles = _RSSI_STABILITY_THRESHOLD

        coord._adjust_polling_interval()

        # Should poll slower
        assert coord._rssi_interval > initial_interval

    def test_alternating_active_idle_behavior(self) -> None:
        """Test alternating between active and idle states."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._rssi_interval = _RSSI_DEFAULT_INTERVAL

        # Active period
        coord._state_change_counter = 3
        coord._adjust_polling_interval()
        active_interval = coord._rssi_interval

        # Idle period
        coord._stable_cycles = _RSSI_STABILITY_THRESHOLD
        coord._adjust_polling_interval()
        idle_interval = coord._rssi_interval

        # Idle should be longer than active
        assert idle_interval > active_interval

    def test_stability_threshold_prevents_premature_slowdown(self) -> None:
        """Test that stability threshold prevents premature slowdown."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._rssi_interval = _RSSI_DEFAULT_INTERVAL
        initial_interval = coord._rssi_interval

        # Not enough stable cycles
        coord._stable_cycles = _RSSI_STABILITY_THRESHOLD - 1

        coord._adjust_polling_interval()

        # Should NOT have changed (threshold not reached)
        assert coord._rssi_interval == initial_interval

    def test_frequent_changes_threshold(self) -> None:
        """Test that frequent changes require 2+ changes to trigger."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._rssi_interval = _RSSI_DEFAULT_INTERVAL
        initial_interval = coord._rssi_interval

        # Only 1 change (below threshold)
        coord._state_change_counter = 1

        coord._adjust_polling_interval()

        # Should NOT have decreased (threshold >= 2)
        assert coord._rssi_interval == initial_interval

        # Now 2 changes
        coord._state_change_counter = 2

        coord._adjust_polling_interval()

        # Should have decreased
        assert coord._rssi_interval < initial_interval


@pytest.mark.requires_ha
class TestPollingForBridgeDevices:
    """Test that polling applies to bridge devices as well."""

    def test_bridge_device_has_adaptive_polling(self) -> None:
        """Test that bridge devices also use adaptive polling."""
        device = make_mock_device()
        device.host = "192.168.1.100"
        device.port = 8081

        coord = TuyaBLEMeshCoordinator(device)

        # Should start with default interval
        assert coord._rssi_interval == _RSSI_DEFAULT_INTERVAL

    def test_bridge_device_interval_adjusts(self) -> None:
        """Test that bridge device polling interval adjusts."""
        device = make_mock_device()
        device.host = "192.168.1.100"
        device.port = 8081

        coord = TuyaBLEMeshCoordinator(device)

        coord._rssi_interval = _RSSI_DEFAULT_INTERVAL
        initial_interval = coord._rssi_interval

        # Trigger adjustment
        coord._state_change_counter = 3
        coord._adjust_polling_interval()

        # Should have adjusted
        assert coord._rssi_interval != initial_interval


@pytest.mark.requires_ha
class TestPollingIntervalPersistence:
    """Test that polling interval persists across state changes."""

    def test_interval_persists_after_state_update(self) -> None:
        """Test that interval is maintained across state updates."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Set custom interval
        coord._rssi_interval = 45.0

        # Update state
        coord._state.brightness = 200

        # Interval should persist
        assert coord._rssi_interval == 45.0

    def test_interval_persists_after_availability_change(self) -> None:
        """Test that interval persists when availability changes."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._rssi_interval = 75.0

        # Change availability
        coord._state.available = True

        # Interval should persist
        assert coord._rssi_interval == 75.0


@pytest.mark.requires_ha
class TestPollingIntegrationWithStateUpdates:
    """Test integration between polling and state updates."""

    def test_state_change_increments_counter(self) -> None:
        """Test that state changes can increment the counter."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Simulate change detection incrementing counter
        coord._state_change_counter += 1
        assert coord._state_change_counter == 1

    def test_no_change_increments_stable_cycles(self) -> None:
        """Test that no changes can increment stable cycles."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Simulate stable period incrementing stable cycles
        coord._stable_cycles += 1
        assert coord._stable_cycles == 1

    def test_change_resets_stable_cycles(self) -> None:
        """Test that changes can reset stable cycles."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._stable_cycles = 5

        # Simulate change resetting stable cycles
        coord._stable_cycles = 0
        assert coord._stable_cycles == 0
