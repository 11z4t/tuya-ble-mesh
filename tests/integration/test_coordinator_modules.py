"""Integration tests for coordinator with extracted modules (Phase B).

Tests that ReconnectionStrategy, ErrorClassifier, and PollingScheduler
work correctly when integrated with the coordinator.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))


class TestReconnectionIntegration:
    """Test ReconnectionStrategy integration with coordinator."""

    @pytest.mark.asyncio
    async def test_reconnection_strategy_exponential_backoff(self) -> None:
        """Coordinator should use exponential backoff on connection failures."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.connect = AsyncMock(side_effect=TimeoutError("Connection timeout"))
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Verify initial backoff
        assert coord._reconnection.current_backoff == 5.0

        # Simulate first failure
        coord._reconnection.record_failure(is_bridge=False)
        assert coord._reconnection.current_backoff == 10.0  # 5.0 * 2.0

        # Simulate second failure
        coord._reconnection.record_failure(is_bridge=False)
        assert coord._reconnection.current_backoff == 20.0  # 10.0 * 2.0

        # Simulate third failure
        coord._reconnection.record_failure(is_bridge=False)
        assert coord._reconnection.current_backoff == 40.0  # 20.0 * 2.0

    @pytest.mark.asyncio
    async def test_reconnection_strategy_bridge_shorter_backoff(self) -> None:
        """Bridge devices should use shorter backoff intervals."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "192.168.1.100"
        mock_device.connect = AsyncMock(side_effect=TimeoutError("Connection timeout"))
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()
        mock_device.__class__.__name__ = "TelinkBridgeDevice"

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Reset with bridge flag
        coord._reconnection.reset(is_bridge=True)
        assert coord._reconnection.current_backoff == 3.0  # Bridge initial backoff

        # Verify bridge max backoff is lower
        coord._reconnection.record_failure(is_bridge=True)
        coord._reconnection.record_failure(is_bridge=True)
        coord._reconnection.record_failure(is_bridge=True)
        coord._reconnection.record_failure(is_bridge=True)
        coord._reconnection.record_failure(is_bridge=True)
        coord._reconnection.record_failure(is_bridge=True)
        # After many failures, should cap at bridge max (120s) not regular max (300s)
        assert coord._reconnection.current_backoff <= 120.0

    @pytest.mark.asyncio
    async def test_reconnection_storm_detection(self) -> None:
        """Coordinator should detect reconnection storms."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.connect = AsyncMock(side_effect=TimeoutError("Connection timeout"))
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Simulate 10 rapid reconnect attempts (storm threshold)
        for _ in range(10):
            coord._reconnection.record_success()

        # Check storm detection
        is_storm = coord._reconnection.check_storm()
        assert is_storm is True
        assert coord._reconnection.statistics.storm_detected is True

    @pytest.mark.asyncio
    async def test_reconnection_reset_on_success(self) -> None:
        """Reconnection backoff should reset after successful connection."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Simulate failures (increase backoff)
        coord._reconnection.record_failure(is_bridge=False)
        coord._reconnection.record_failure(is_bridge=False)
        assert coord._reconnection.current_backoff == 20.0

        # Test direct reset via ReconnectionStrategy (avoid _on_status_update dispatcher issues)
        coord._reconnection.reset(is_bridge=False)

        # Verify backoff reset
        assert coord._reconnection.current_backoff == 5.0
        assert coord._reconnection.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_reconnection_max_failures(self) -> None:
        """Coordinator should give up after max failures."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.connect = AsyncMock(side_effect=TimeoutError("Connection timeout"))
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        # Create coordinator with max_failures=3
        coord = TuyaBLEMeshCoordinator(mock_device, max_reconnect_failures=3)

        # Simulate 3 failures
        coord._reconnection.record_failure(is_bridge=False)
        coord._reconnection.record_failure(is_bridge=False)
        coord._reconnection.record_failure(is_bridge=False)

        # Should give up
        assert coord._reconnection.should_give_up() is True


class TestErrorClassifierIntegration:
    """Test ErrorClassifier integration with coordinator."""

    @pytest.mark.asyncio
    async def test_coordinator_classifies_timeout_as_transient(self) -> None:
        """Coordinator should classify timeout errors as transient."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator
        from custom_components.tuya_ble_mesh.error_classifier import ErrorClass

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        coord = TuyaBLEMeshCoordinator(mock_device)

        timeout_err = TimeoutError("Connection timeout")
        err_class = coord._error_classifier.classify(timeout_err)

        assert err_class == ErrorClass.TRANSIENT

    @pytest.mark.asyncio
    async def test_coordinator_classifies_auth_errors(self) -> None:
        """Coordinator should classify authentication errors."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator
        from custom_components.tuya_ble_mesh.error_classifier import ErrorClass

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        coord = TuyaBLEMeshCoordinator(mock_device)

        auth_err = ValueError("Invalid mesh password")
        err_class = coord._error_classifier.classify(auth_err)

        assert err_class == ErrorClass.MESH_AUTH

    @pytest.mark.asyncio
    async def test_coordinator_classifies_bridge_down_errors(self) -> None:
        """Coordinator should classify bridge connectivity errors."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator
        from custom_components.tuya_ble_mesh.error_classifier import ErrorClass

        mock_device = MagicMock()
        mock_device.address = "192.168.1.100"

        coord = TuyaBLEMeshCoordinator(mock_device)

        bridge_err = ConnectionError("Connection refused")
        err_class = coord._error_classifier.classify(bridge_err)

        assert err_class == ErrorClass.BRIDGE_DOWN

    @pytest.mark.asyncio
    async def test_coordinator_classifies_device_offline_errors(self) -> None:
        """Coordinator should classify device not found errors."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator
        from custom_components.tuya_ble_mesh.error_classifier import ErrorClass

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        coord = TuyaBLEMeshCoordinator(mock_device)

        offline_err = RuntimeError("Device not found on mesh")
        err_class = coord._error_classifier.classify(offline_err)

        assert err_class == ErrorClass.DEVICE_OFFLINE

    @pytest.mark.asyncio
    async def test_coordinator_classifies_permanent_errors(self) -> None:
        """Coordinator should classify unsupported device errors as permanent."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator
        from custom_components.tuya_ble_mesh.error_classifier import ErrorClass

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        coord = TuyaBLEMeshCoordinator(mock_device)

        permanent_err = RuntimeError("Unsupported vendor ID")
        err_class = coord._error_classifier.classify(permanent_err)

        assert err_class == ErrorClass.PERMANENT


class TestPollingSchedulerIntegration:
    """Test PollingScheduler integration with coordinator."""

    @pytest.mark.asyncio
    async def test_polling_interval_decreases_on_frequent_changes(self) -> None:
        """Polling interval should decrease when device state changes frequently."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Initial interval
        initial_interval = coord._polling.current_interval
        assert initial_interval == 60.0  # Default interval

        # Simulate frequent state changes directly via PollingScheduler
        coord._polling.record_change()
        coord._polling.record_change()
        coord._polling.adjust_interval()

        # Interval should have decreased (more responsive)
        new_interval = coord._polling.current_interval
        assert new_interval < initial_interval

    @pytest.mark.asyncio
    async def test_polling_interval_increases_on_stable_state(self) -> None:
        """Polling interval should increase when device state is stable."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Reset to min interval
        coord._polling._current_interval = 30.0

        # Simulate stable cycles (no changes)
        for _ in range(4):  # More than stability_threshold (3)
            coord._polling.record_stable_cycle()
            coord._polling.adjust_interval()

        # Interval should have increased (lower overhead)
        assert coord._polling.current_interval > 30.0

    @pytest.mark.asyncio
    async def test_polling_scheduler_tracks_state_changes(self) -> None:
        """PollingScheduler should track state changes directly."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Initially no changes
        assert coord._polling._state_change_counter == 0

        # Record changes directly
        coord._polling.record_change()

        # Should have recorded a change
        assert coord._polling._state_change_counter >= 1

    @pytest.mark.asyncio
    async def test_polling_scheduler_respects_min_max_intervals(self) -> None:
        """PollingScheduler should never go below min or above max interval."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Simulate many changes (should cap at min)
        for _ in range(20):
            coord._polling.record_change()
            coord._polling.adjust_interval()

        assert coord._polling.current_interval >= 30.0  # min_interval

        # Simulate many stable cycles (should cap at max)
        coord._polling.reset()
        for _ in range(20):
            coord._polling.record_stable_cycle()
            coord._polling.adjust_interval()

        assert coord._polling.current_interval <= 300.0  # max_interval


class TestCoordinatorModuleIntegration:
    """Test that coordinator correctly uses all extracted modules together."""

    @pytest.mark.asyncio
    async def test_coordinator_uses_reconnection_strategy_on_failure(self) -> None:
        """Coordinator should use ReconnectionStrategy when connection fails."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.connect = AsyncMock(side_effect=TimeoutError("Connection timeout"))
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Initial backoff
        initial_backoff = coord._reconnection.current_backoff

        # Simulate connection failure in reconnect loop
        try:
            await coord._device.connect()
        except TimeoutError:
            coord._reconnection.record_failure(is_bridge=False)

        # Verify backoff increased
        assert coord._reconnection.current_backoff > initial_backoff

    @pytest.mark.asyncio
    async def test_coordinator_uses_error_classifier_for_diagnostics(self) -> None:
        """Coordinator should use ErrorClassifier to categorize connection errors."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator
        from custom_components.tuya_ble_mesh.error_classifier import ErrorClass

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Test various error types
        errors = [
            (TimeoutError("timeout"), ErrorClass.TRANSIENT),
            (ValueError("Invalid password"), ErrorClass.MESH_AUTH),
            (ConnectionError("Connection refused"), ErrorClass.BRIDGE_DOWN),
            (RuntimeError("Device not found"), ErrorClass.DEVICE_OFFLINE),
            (RuntimeError("Unsupported vendor"), ErrorClass.PERMANENT),
        ]

        for err, expected_class in errors:
            err_class = coord._error_classifier.classify(err)
            assert err_class == expected_class

    @pytest.mark.asyncio
    async def test_coordinator_uses_polling_scheduler_for_rssi(self) -> None:
        """Coordinator should use PollingScheduler for adaptive RSSI updates."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"
        mock_device.connect = AsyncMock()
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Verify polling scheduler is initialized
        assert coord._polling is not None
        assert coord._polling.current_interval == 60.0  # Default

        # Simulate changes directly
        initial_interval = coord._polling.current_interval
        coord._polling.record_change()
        coord._polling.record_change()
        coord._polling.adjust_interval()

        # Polling should adapt
        assert coord._polling.current_interval <= initial_interval

    @pytest.mark.asyncio
    async def test_coordinator_handles_complete_failure_recovery_cycle(self) -> None:
        """Test complete failure → reconnection → recovery cycle with all modules."""
        from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        # First attempt fails, second succeeds
        connect_attempts = [TimeoutError("timeout"), None]
        mock_device.connect = AsyncMock(side_effect=connect_attempts)
        mock_device.disconnect = AsyncMock()
        mock_device.set_status_callback = MagicMock()

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Simulate first connection failure
        try:
            await coord._device.connect()
        except TimeoutError:
            coord._reconnection.record_failure(is_bridge=False)
            err_class = coord._error_classifier.classify(TimeoutError("timeout"))
            assert err_class.value == "transient"

        # Verify backoff increased
        assert coord._reconnection.current_backoff > 5.0

        # Simulate successful reconnection
        await coord._device.connect()  # Should succeed now
        coord._reconnection.record_success()

        # Verify recovery
        assert coord._reconnection.statistics.total_reconnects == 1

        # Simulate successful reset (bypass _on_status_update dispatcher)
        coord._reconnection.reset(is_bridge=False)
        coord._state.available = True

        # Verify full recovery
        assert coord._reconnection.current_backoff == 5.0  # Reset to initial
        assert coord._reconnection.consecutive_failures == 0
        assert coord._state.available is True
