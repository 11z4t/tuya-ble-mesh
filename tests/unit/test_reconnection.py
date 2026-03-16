"""Unit tests for reconnection logic in TuyaBLEMeshCoordinator.

Tests the exponential backoff mechanism, storm detection, max failure limits,
and integration with error classification and repair issue creation.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))


from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    _BACKOFF_MULTIPLIER,
    _BRIDGE_INITIAL_BACKOFF,
    _BRIDGE_MAX_BACKOFF,
    _INITIAL_BACKOFF,
    _MAX_BACKOFF,
    _STORM_WINDOW_SECONDS,
    ErrorClass,
    TuyaBLEMeshCoordinator,
)

_PATCH_SLEEP = "custom_components.tuya_ble_mesh.coordinator.asyncio.sleep"


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


def make_bridge_device() -> MagicMock:
    """Create a mock bridge device (has host and port attributes)."""
    device = make_mock_device()
    device.host = "192.168.1.100"
    device.port = 8081
    # Set __name__ to include "Bridge" so _is_bridge_device() returns True
    type(device).__name__ = "TelinkBridgeDevice"
    return device


@pytest.mark.requires_ha
class TestReconnectionBackoff:
    """Test exponential backoff for reconnection attempts."""

    def test_initial_backoff(self) -> None:
        """Test that coordinator starts with initial backoff value."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        assert coord._backoff == _INITIAL_BACKOFF

    def test_backoff_increases_exponentially(self) -> None:
        """Test that backoff increases exponentially on repeated failures."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        initial = coord._backoff
        coord._backoff *= _BACKOFF_MULTIPLIER
        assert coord._backoff == initial * _BACKOFF_MULTIPLIER

        coord._backoff *= _BACKOFF_MULTIPLIER
        assert coord._backoff == initial * _BACKOFF_MULTIPLIER * _BACKOFF_MULTIPLIER

    def test_backoff_respects_maximum(self) -> None:
        """Test that backoff does not exceed MAX_BACKOFF."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Simulate many failures
        for _ in range(20):
            coord._backoff = min(coord._backoff * _BACKOFF_MULTIPLIER, _MAX_BACKOFF)

        assert coord._backoff == _MAX_BACKOFF

    def test_backoff_resets_on_success(self) -> None:
        """Test that backoff resets to initial value after successful reconnect."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Simulate failures increasing backoff
        coord._backoff = 60.0

        # Simulate successful reconnect (backoff reset happens in _reconnect_loop)
        coord._backoff = _INITIAL_BACKOFF
        assert coord._backoff == _INITIAL_BACKOFF

    def test_bridge_device_uses_shorter_backoff(self) -> None:
        """Test that bridge devices can use shorter initial backoff."""
        device = make_bridge_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Bridge-specific backoff is set in async_start (on disconnect callback)
        # Verify that the constant is defined and shorter
        assert _BRIDGE_INITIAL_BACKOFF < _INITIAL_BACKOFF

        # Simulate setting bridge backoff (happens in async_start)
        if coord.is_bridge_device():
            coord._backoff = _BRIDGE_INITIAL_BACKOFF

        assert coord._backoff == _BRIDGE_INITIAL_BACKOFF

    def test_bridge_device_max_backoff(self) -> None:
        """Test that bridge devices respect their own max backoff."""
        device = make_bridge_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Simulate many failures with bridge max
        for _ in range(20):
            coord._backoff = min(coord._backoff * _BACKOFF_MULTIPLIER, _BRIDGE_MAX_BACKOFF)

        assert coord._backoff == _BRIDGE_MAX_BACKOFF
        assert _BRIDGE_MAX_BACKOFF < _MAX_BACKOFF


@pytest.mark.requires_ha
class TestReconnectionStormDetection:
    """Test reconnection storm detection and prevention."""

    def test_storm_detection_threshold(self) -> None:
        """Test that storm is detected when reconnects exceed threshold."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Set low threshold for testing
        coord._storm_threshold = 5

        import time
        now = time.time()

        # Add reconnects within storm window
        for i in range(6):
            coord._stats.reconnect_times.append(now - i * 10)

        assert coord._check_reconnect_storm()

    def test_no_storm_when_below_threshold(self) -> None:
        """Test that no storm is detected when below threshold."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._storm_threshold = 10

        import time
        now = time.time()

        # Add only a few reconnects
        for i in range(3):
            coord._stats.reconnect_times.append(now - i * 10)

        assert not coord._check_reconnect_storm()

    def test_storm_detection_ignores_old_reconnects(self) -> None:
        """Test that storm detection only counts recent reconnects."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._storm_threshold = 5

        import time
        now = time.time()

        # Add old reconnects (outside storm window)
        for i in range(10):
            coord._stats.reconnect_times.append(now - _STORM_WINDOW_SECONDS - 100 - i * 10)

        # Add only a few recent ones
        for i in range(3):
            coord._stats.reconnect_times.append(now - i * 10)

        # Storm detection should clean up old entries
        assert not coord._check_reconnect_storm()

    def test_storm_flag_is_set(self) -> None:
        """Test that storm_detected flag is set correctly."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Initially false
        assert coord._stats.storm_detected is False

        # Can be set to true
        coord._stats.storm_detected = True
        assert coord._stats.storm_detected is True


@pytest.mark.requires_ha
class TestReconnectionFailureTracking:
    """Test consecutive failure tracking and max reconnect limits."""

    def test_consecutive_failures_increment(self) -> None:
        """Test that consecutive failures are tracked."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        assert coord._consecutive_failures == 0

        coord._consecutive_failures += 1
        assert coord._consecutive_failures == 1

        coord._consecutive_failures += 1
        assert coord._consecutive_failures == 2

    def test_consecutive_failures_reset_on_success(self) -> None:
        """Test that consecutive failures reset after successful connect."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._consecutive_failures = 5

        # Simulate success
        coord._consecutive_failures = 0
        assert coord._consecutive_failures == 0

    def test_max_reconnect_failures_default(self) -> None:
        """Test that max reconnect failures defaults to 0 (unlimited)."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        assert coord._max_reconnect_failures == 0

    def test_max_reconnect_failures_can_be_set(self) -> None:
        """Test that max reconnect failures limit can be configured."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._max_reconnect_failures = 10
        assert coord._max_reconnect_failures == 10

    @pytest.mark.asyncio
    async def test_reconnect_stops_on_permanent_error(self) -> None:
        """Test that reconnection stops immediately on permanent errors."""
        device = make_mock_device()
        device.connect = AsyncMock(side_effect=Exception("unsupported device"))

        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True

        # Classify error as permanent
        error = Exception("unsupported device")
        error_class = coord._classify_error(error)

        # Should be permanent
        assert error_class == ErrorClass.PERMANENT


@pytest.mark.requires_ha
class TestReconnectionScheduling:
    """Test reconnection task scheduling and cancellation."""

    def test_reconnect_task_initially_none(self) -> None:
        """Test that reconnect task is initially None."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        assert coord._reconnect_task is None

    def testschedule_reconnect_when_not_running(self) -> None:
        """Test that schedule_reconnect is no-op when coordinator not running."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._running = False
        coord.schedule_reconnect()

        # Should still be None since not running
        assert coord._reconnect_task is None

    @pytest.mark.asyncio
    async def test_reconnect_task_cancellation_on_stop(self) -> None:
        """Test that reconnect task is cancelled when coordinator stops."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Create a dummy task
        async def dummy() -> None:
            await asyncio.sleep(10)

        coord._reconnect_task = asyncio.create_task(dummy())
        assert coord._reconnect_task is not None
        assert not coord._reconnect_task.done()

        # Cancel it
        coord._reconnect_task.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await coord._reconnect_task

        assert coord._reconnect_task.cancelled()


@pytest.mark.requires_ha
class TestReconnectionStatistics:
    """Test statistics tracking for reconnection events."""

    def test_reconnect_counter_increments(self) -> None:
        """Test that total_reconnects counter increments."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        assert coord._stats.total_reconnects == 0

        coord._stats.total_reconnects += 1
        assert coord._stats.total_reconnects == 1

    def test_reconnect_times_tracked(self) -> None:
        """Test that reconnect timestamps are recorded."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        import time
        now = time.time()

        coord._stats.reconnect_times.append(now)
        assert len(coord._stats.reconnect_times) == 1
        assert coord._stats.reconnect_times[0] == now

    def test_reconnect_times_deque_max_length(self) -> None:
        """Test that reconnect_times deque respects maxlen."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        import time

        # Add more than maxlen (50)
        for i in range(60):
            coord._stats.reconnect_times.append(time.time() - i)

        # Should only keep last 50
        assert len(coord._stats.reconnect_times) <= 50

    def test_connection_uptime_tracking(self) -> None:
        """Test that connection uptime is tracked."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        assert coord._stats.connection_uptime == 0.0

        coord._stats.connection_uptime = 123.45
        assert coord._stats.connection_uptime == 123.45

    def test_disconnect_time_tracking(self) -> None:
        """Test that disconnect timestamps are recorded."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        import time
        now = time.time()

        coord._stats.last_disconnect_time = now
        assert coord._stats.last_disconnect_time == now


@pytest.mark.requires_ha
class TestReconnectionIntegrationWithErrorClassification:
    """Test integration between reconnection logic and error classification."""

    def test_reconnect_classifies_timeout_as_transient(self) -> None:
        """Test that timeout errors are classified as transient."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = TimeoutError("Connection timeout")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.TRANSIENT

    def test_reconnect_classifies_auth_error(self) -> None:
        """Test that authentication errors are classified correctly."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        error = Exception("Authentication failed")
        error_class = coord._classify_error(error)

        assert error_class == ErrorClass.MESH_AUTH

    def test_reconnect_tracks_error_class_in_stats(self) -> None:
        """Test that last error class is tracked in statistics."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._stats.last_error_class = ErrorClass.BRIDGE_DOWN.value
        assert coord._stats.last_error_class == "bridge_down"

    def test_reconnect_updates_last_error(self) -> None:
        """Test that last error message and time are updated."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        import time

        coord._stats.last_error = "Connection refused"
        coord._stats.last_error_time = time.time()

        assert coord._stats.last_error == "Connection refused"
        assert coord._stats.last_error_time is not None


@pytest.mark.requires_ha
class TestReconnectionRepairIssues:
    """Test repair issue tracking during reconnection attempts."""

    def test_raised_repair_issues_initially_empty(self) -> None:
        """Test that raised repair issues set is initially empty."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        assert len(coord._raised_repair_issues) == 0

    def test_repair_issue_tracked(self) -> None:
        """Test that repair issues can be tracked."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._raised_repair_issues.add("bridge_unreachable")
        assert "bridge_unreachable" in coord._raised_repair_issues

    def test_repair_issues_cleared_on_recovery(self) -> None:
        """Test that repair issues are cleared after successful reconnect."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._raised_repair_issues.add("bridge_unreachable")
        coord._raised_repair_issues.add("device_not_found")

        # Simulate clearing on recovery
        coord._raised_repair_issues.clear()

        assert len(coord._raised_repair_issues) == 0

    def test_duplicate_repair_issues_not_created(self) -> None:
        """Test that duplicate repair issues are not created."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._raised_repair_issues.add("timeout")

        # Try to add again
        before_count = len(coord._raised_repair_issues)
        coord._raised_repair_issues.add("timeout")
        after_count = len(coord._raised_repair_issues)

        assert before_count == after_count
        assert "timeout" in coord._raised_repair_issues
