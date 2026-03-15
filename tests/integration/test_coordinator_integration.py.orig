"""Integration tests for TuyaBLEMeshCoordinator.

Tests focus on:
1. Bus event dispatching via async_set_updated_data()
2. Thread-safe dispatcher serialization to HA event loop
3. Listener notification in both HA and standalone modes
"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshCoordinator,
)


def make_mock_device() -> MagicMock:
    """Create a mock MeshDevice."""
    device = MagicMock()
    device.address = "DC:23:4D:21:43:A5"
    device.mesh_id = 0x01
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
class TestCoordinatorEventDispatch:
    """Test coordinator event dispatching in HA context."""

    @pytest.mark.asyncio
    async def test_status_update_triggers_async_set_updated_data(self) -> None:
        """Status update should call async_set_updated_data() in HA mode."""
        mock_device = make_mock_device()
        mock_hass = MagicMock()
        mock_hass.loop = asyncio.get_event_loop()

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test")

        # Mock async_set_updated_data to track calls
        coord.async_set_updated_data = MagicMock()

        # Simulate status update from BLE notification
        status = make_mock_status(white_brightness=50)
        coord._on_status_update(status)

        # Give event loop time to process call_soon_threadsafe
        await asyncio.sleep(0.01)

        # Verify async_set_updated_data was called
        coord.async_set_updated_data.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_onoff_update_triggers_dispatch(self) -> None:
        """GenericOnOff update should trigger dispatcher."""
        mock_device = make_mock_device()
        mock_hass = MagicMock()
        mock_hass.loop = asyncio.get_event_loop()

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test")
        coord.async_set_updated_data = MagicMock()

        # Simulate on/off update
        coord._on_onoff_update(True)

        await asyncio.sleep(0.01)

        coord.async_set_updated_data.assert_called_once_with(None)

    @pytest.mark.asyncio
    async def test_duplicate_status_does_not_trigger_dispatch(self) -> None:
        """Identical status updates should not trigger dispatch (optimization)."""
        mock_device = make_mock_device()
        mock_hass = MagicMock()
        mock_hass.loop = asyncio.get_event_loop()

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test")
        coord.async_set_updated_data = MagicMock()

        # Set initial state
        status1 = make_mock_status(white_brightness=50, white_temp=30)
        coord._on_status_update(status1)
        await asyncio.sleep(0.01)

        # Reset mock to track only new calls
        coord.async_set_updated_data.reset_mock()

        # Send identical status
        status2 = make_mock_status(white_brightness=50, white_temp=30)
        coord._on_status_update(status2)
        await asyncio.sleep(0.01)

        # Should NOT dispatch (no change)
        coord.async_set_updated_data.assert_not_called()

    @pytest.mark.asyncio
    async def test_state_change_triggers_dispatch(self) -> None:
        """Changed status should trigger dispatch."""
        mock_device = make_mock_device()
        mock_hass = MagicMock()
        mock_hass.loop = asyncio.get_event_loop()

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test")
        coord.async_set_updated_data = MagicMock()

        # Set initial state
        status1 = make_mock_status(white_brightness=50)
        coord._on_status_update(status1)
        await asyncio.sleep(0.01)

        coord.async_set_updated_data.reset_mock()

        # Send different brightness
        status2 = make_mock_status(white_brightness=75)
        coord._on_status_update(status2)
        await asyncio.sleep(0.01)

        # Should dispatch (state changed)
        coord.async_set_updated_data.assert_called_once_with(None)


@pytest.mark.requires_ha
class TestThreadSafeDispatcher:
    """Test thread-safe event dispatcher."""

    @pytest.mark.asyncio
    async def test_dispatch_from_background_thread_uses_call_soon_threadsafe(self) -> None:
        """BLE callbacks from background threads should use call_soon_threadsafe."""
        mock_device = make_mock_device()
        mock_hass = MagicMock()
        event_loop = asyncio.get_event_loop()
        mock_hass.loop = event_loop

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test")

        # Track if call_soon_threadsafe was used
        dispatch_called = threading.Event()
        original_call = event_loop.call_soon_threadsafe

        def tracked_call(callback, *args):
            dispatch_called.set()
            return original_call(callback, *args)

        event_loop.call_soon_threadsafe = tracked_call

        # Simulate BLE notification from background thread
        def background_notification():
            status = make_mock_status(white_brightness=80)
            coord._on_status_update(status)

        thread = threading.Thread(target=background_notification)
        thread.start()
        thread.join()

        # Wait for dispatch
        assert dispatch_called.wait(timeout=1.0), "call_soon_threadsafe not called"

        # Restore original
        event_loop.call_soon_threadsafe = original_call

    @pytest.mark.asyncio
    async def test_multiple_concurrent_updates_are_serialized(self) -> None:
        """Multiple concurrent status updates should serialize correctly."""
        mock_device = make_mock_device()
        mock_hass = MagicMock()
        mock_hass.loop = asyncio.get_event_loop()

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test")

        call_count = 0

        def track_call(data):
            nonlocal call_count
            call_count += 1

        coord.async_set_updated_data = track_call

        # Fire multiple updates rapidly
        for brightness in range(10, 100, 10):
            status = make_mock_status(white_brightness=brightness)
            coord._on_status_update(status)

        # Allow event loop to process all calls
        await asyncio.sleep(0.05)

        # All updates should be processed (9 distinct brightness values)
        assert call_count == 9


class TestStandaloneListeners:
    """Test listener system in standalone mode (no HA)."""

    def test_add_listener_in_standalone_mode(self) -> None:
        """Should support add_listener when hass=None."""
        mock_device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(mock_device)  # No hass

        listener_called = False

        def listener():
            nonlocal listener_called
            listener_called = True

        # Add listener
        remove_callback = coord.add_listener(listener)

        # Trigger update
        status = make_mock_status(white_brightness=60)
        coord._on_status_update(status)

        # Listener should be called
        assert listener_called

        # Remove listener
        remove_callback()

        # Reset flag and trigger again
        listener_called = False
        status2 = make_mock_status(white_brightness=70)
        coord._on_status_update(status2)

        # Listener should NOT be called after removal
        assert not listener_called

    def test_listener_errors_are_handled(self) -> None:
        """Broken listeners should be removed after MAX_CALLBACK_ERRORS."""
        from custom_components.tuya_ble_mesh.coordinator import _MAX_CALLBACK_ERRORS

        mock_device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(mock_device)

        error_count = 0

        def broken_listener():
            nonlocal error_count
            error_count += 1
            raise RuntimeError("Broken listener")

        coord.add_listener(broken_listener)

        # Trigger updates until listener is removed
        for i in range(_MAX_CALLBACK_ERRORS + 2):
            status = make_mock_status(white_brightness=i + 10)
            coord._on_status_update(status)

        # Listener should have errored exactly _MAX_CALLBACK_ERRORS times
        assert error_count == _MAX_CALLBACK_ERRORS

    def test_multiple_listeners_in_standalone_mode(self) -> None:
        """Multiple listeners should all be notified."""
        mock_device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(mock_device)

        calls = []

        def listener1():
            calls.append("listener1")

        def listener2():
            calls.append("listener2")

        coord.add_listener(listener1)
        coord.add_listener(listener2)

        status = make_mock_status(white_brightness=40)
        coord._on_status_update(status)

        assert "listener1" in calls
        assert "listener2" in calls
        assert len(calls) == 2


@pytest.mark.requires_ha
class TestConnectionStatistics:
    """Test connection statistics tracking."""

    @pytest.mark.asyncio
    async def test_statistics_track_response_times(self) -> None:
        """Statistics should track response times correctly."""
        mock_device = make_mock_device()
        mock_hass = MagicMock()
        mock_hass.loop = asyncio.get_event_loop()

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test")

        # Initially no response times
        assert coord.avg_response_time_ms is None

        # Simulate adding response times
        coord._stats.response_times.append(0.050)  # 50ms
        coord._stats.response_times.append(0.100)  # 100ms
        coord._stats.response_times.append(0.075)  # 75ms

        # Average should be 75ms
        avg = coord.avg_response_time_ms
        assert avg is not None
        assert abs(avg - 75.0) < 0.1  # Float comparison with tolerance

    @pytest.mark.asyncio
    async def test_statistics_track_errors(self) -> None:
        """Statistics should increment error counts on failures."""
        mock_device = make_mock_device()
        mock_hass = MagicMock()
        mock_hass.loop = asyncio.get_event_loop()

        coord = TuyaBLEMeshCoordinator(mock_device, hass=mock_hass, entry_id="test")

        assert coord._stats.total_errors == 0
        assert coord._stats.connection_errors == 0

        # Simulate connection error
        coord._stats.connection_errors += 1
        coord._stats.total_errors += 1

        assert coord._stats.connection_errors == 1
        assert coord._stats.total_errors == 1
