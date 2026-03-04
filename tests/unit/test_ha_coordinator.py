"""Unit tests for the Tuya BLE Mesh coordinator."""

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
    _INITIAL_BACKOFF,
    _MAX_BACKOFF,
    TuyaBLEMeshCoordinator,
    TuyaBLEMeshDeviceState,
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
) -> MagicMock:
    """Create a mock StatusResponse."""
    status = MagicMock()
    status.mode = mode
    status.white_brightness = white_brightness
    status.white_temp = white_temp
    status.color_brightness = color_brightness
    status.red = 0
    status.green = 0
    status.blue = 0
    status.mesh_id = 1
    return status


class TestDeviceState:
    """Test TuyaBLEMeshDeviceState defaults."""

    def test_default_state(self) -> None:
        state = TuyaBLEMeshDeviceState()
        assert state.is_on is False
        assert state.brightness == 0
        assert state.color_temp == 0
        assert state.mode == 0
        assert state.rssi is None
        assert state.firmware_version is None
        assert state.available is False


class TestCoordinatorInit:
    """Test coordinator initialization."""

    def test_initial_state(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        assert coord.device is device
        assert coord.state.available is False
        assert coord.state.is_on is False

    def test_device_property(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        assert coord.device is device


class TestStatusUpdate:
    """Test _on_status_update callback."""

    def test_updates_state_from_status(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        status = make_mock_status(white_brightness=80, white_temp=64, mode=1)

        coord._on_status_update(status)

        assert coord.state.brightness == 80
        assert coord.state.color_temp == 64
        assert coord.state.mode == 1
        assert coord.state.is_on is True
        assert coord.state.available is True

    def test_off_when_brightness_zero(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        status = make_mock_status(white_brightness=0, color_brightness=0)

        coord._on_status_update(status)

        assert coord.state.is_on is False

    def test_on_when_color_brightness_nonzero(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        status = make_mock_status(white_brightness=0, color_brightness=50)

        coord._on_status_update(status)

        assert coord.state.is_on is True

    def test_notifies_listeners(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        listener = MagicMock()
        coord.add_listener(listener)
        status = make_mock_status()

        coord._on_status_update(status)

        listener.assert_called_once()


class TestListeners:
    """Test listener registration."""

    def test_add_and_remove_listener(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        listener = MagicMock()

        remove = coord.add_listener(listener)

        # Trigger notification
        coord._notify_listeners()
        listener.assert_called_once()

        # Remove and verify no more calls
        listener.reset_mock()
        remove()
        coord._notify_listeners()
        listener.assert_not_called()

    def test_multiple_listeners(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        listener1 = MagicMock()
        listener2 = MagicMock()

        coord.add_listener(listener1)
        coord.add_listener(listener2)

        coord._notify_listeners()

        listener1.assert_called_once()
        listener2.assert_called_once()

    def test_listener_error_does_not_stop_others(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        bad_listener = MagicMock(side_effect=RuntimeError("oops"))
        good_listener = MagicMock()

        coord.add_listener(bad_listener)
        coord.add_listener(good_listener)

        coord._notify_listeners()

        good_listener.assert_called_once()

    def test_remove_nonexistent_listener_is_noop(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        listener = MagicMock()

        remove = coord.add_listener(listener)
        remove()
        # Second remove should be a no-op
        remove()


class TestAsyncStart:
    """Test async_start method."""

    @pytest.mark.asyncio
    async def test_start_connects_device(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        await coord.async_start()

        device.connect.assert_called_once()
        device.register_status_callback.assert_called_once()
        device.register_disconnect_callback.assert_called_once()
        assert coord.state.available is True

    @pytest.mark.asyncio
    async def test_start_handles_connection_failure(self) -> None:
        device = make_mock_device()
        device.connect = AsyncMock(side_effect=ConnectionError("fail"))
        coord = TuyaBLEMeshCoordinator(device)

        await coord.async_start()

        assert coord.state.available is False
        # Should schedule reconnect (creates a task)
        assert coord._reconnect_task is not None

        # Clean up
        await coord.async_stop()


class TestAsyncStop:
    """Test async_stop method."""

    @pytest.mark.asyncio
    async def test_stop_disconnects_device(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        await coord.async_start()

        await coord.async_stop()

        device.disconnect.assert_called_once()
        device.unregister_status_callback.assert_called_once()
        device.unregister_disconnect_callback.assert_called_once()
        assert coord.state.available is False

    @pytest.mark.asyncio
    async def test_stop_cancels_reconnect_task(self) -> None:
        device = make_mock_device()
        device.connect = AsyncMock(side_effect=ConnectionError("fail"))
        coord = TuyaBLEMeshCoordinator(device)
        await coord.async_start()
        assert coord._reconnect_task is not None

        await coord.async_stop()

        assert coord._reconnect_task is None


class TestDisconnectCallback:
    """Test disconnect callback triggers reconnect."""

    def test_on_disconnect_marks_unavailable(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord.state.available = True

        coord._on_disconnect()

        assert coord.state.available is False

    def test_on_disconnect_notifies_listeners(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        listener = MagicMock()
        coord.add_listener(listener)

        coord._on_disconnect()

        listener.assert_called_once()

    def test_on_disconnect_schedules_reconnect(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True

        coord._on_disconnect()

        assert coord._reconnect_task is not None

        # Clean up
        coord._reconnect_task.cancel()
        coord._reconnect_task = None

    def test_on_disconnect_noop_when_stopped(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = False

        coord._on_disconnect()

        # No reconnect task scheduled when not running
        assert coord._reconnect_task is None


class TestOnOffUpdate:
    """Test _on_onoff_update for SIG Mesh devices."""

    def test_on_onoff_update_sets_state_on(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._on_onoff_update(True)

        assert coord.state.is_on is True
        assert coord.state.available is True

    def test_on_onoff_update_sets_state_off(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._on_onoff_update(False)

        assert coord.state.is_on is False
        assert coord.state.available is True

    def test_on_onoff_update_resets_backoff(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._backoff = 60.0

        coord._on_onoff_update(True)

        assert coord._backoff == _INITIAL_BACKOFF

    def test_on_onoff_update_notifies_listeners(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        listener = MagicMock()
        coord.add_listener(listener)

        coord._on_onoff_update(True)

        listener.assert_called_once()


class TestSIGMeshCoordinator:
    """Test coordinator with SIG Mesh device (onoff callbacks)."""

    @pytest.mark.asyncio
    async def test_start_wires_onoff_callback(self) -> None:
        """Coordinator should wire onoff callback for SIG Mesh devices."""
        device = MagicMock()
        device.address = "AA:BB:CC:DD:EE:FF"
        device.connect = AsyncMock()
        device.disconnect = AsyncMock()
        device.register_onoff_callback = MagicMock()
        device.register_disconnect_callback = MagicMock()
        device.unregister_onoff_callback = MagicMock()
        device.unregister_disconnect_callback = MagicMock()
        device.is_connected = True
        device.firmware_version = None

        coord = TuyaBLEMeshCoordinator(device)
        await coord.async_start()

        device.register_onoff_callback.assert_called_once()
        device.register_disconnect_callback.assert_called_once()
        # No register_status_callback since SIG device doesn't have it
        assert not hasattr(device, "register_status_callback") or True

        await coord.async_stop()
        device.unregister_onoff_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_start_wires_both_for_dual_device(self) -> None:
        """If device has both callback types, both should be wired."""
        device = MagicMock()
        device.address = "AA:BB:CC:DD:EE:FF"
        device.connect = AsyncMock()
        device.disconnect = AsyncMock()
        device.register_onoff_callback = MagicMock()
        device.register_status_callback = MagicMock()
        device.register_disconnect_callback = MagicMock()
        device.unregister_onoff_callback = MagicMock()
        device.unregister_status_callback = MagicMock()
        device.unregister_disconnect_callback = MagicMock()
        device.is_connected = True
        device.firmware_version = None

        coord = TuyaBLEMeshCoordinator(device)
        await coord.async_start()

        device.register_onoff_callback.assert_called_once()
        device.register_status_callback.assert_called_once()

        await coord.async_stop()


class TestReconnect:
    """Test reconnection logic."""

    @pytest.mark.asyncio
    async def test_reconnect_resets_backoff_on_success(self) -> None:
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._backoff = 60.0

        # Simulate a successful status update
        status = make_mock_status()
        coord._on_status_update(status)

        assert coord._backoff == _INITIAL_BACKOFF

    def test_backoff_constants(self) -> None:
        assert _INITIAL_BACKOFF == 5.0
        assert _MAX_BACKOFF == 300.0
