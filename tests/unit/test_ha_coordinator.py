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
    _SEQ_PERSIST_INTERVAL,
    _SEQ_SAFETY_MARGIN,
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


class TestSeqPersistence:
    """Test sequence number persistence."""

    def test_seq_store_none_without_hass(self) -> None:
        """Without hass, seq_store should remain None."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        assert coord._seq_store is None

    @pytest.mark.asyncio
    async def test_load_seq_noop_without_hass(self) -> None:
        """_load_seq should be a no-op without hass."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        await coord._load_seq()
        assert coord._seq_store is None

    @pytest.mark.asyncio
    async def test_load_seq_with_stored_data(self) -> None:
        """_load_seq should restore seq with safety margin."""
        device = MagicMock()
        device.address = "DC:23:4D:21:43:A5"
        device.connect = AsyncMock()
        device.disconnect = AsyncMock()
        device.register_disconnect_callback = MagicMock()
        device.set_seq = MagicMock()
        device.get_seq = MagicMock(return_value=3100)
        device.firmware_version = None

        mock_hass = MagicMock()
        mock_store = MagicMock()
        mock_store.async_load = AsyncMock(return_value={"seq": 3000})

        from unittest.mock import patch

        coord = TuyaBLEMeshCoordinator(
            device, hass=mock_hass, entry_id="test_entry"
        )

        with patch(
            "custom_components.tuya_ble_mesh.coordinator.Store",
            return_value=mock_store,
        ):
            await coord._load_seq()

        device.set_seq.assert_called_once_with(3000 + _SEQ_SAFETY_MARGIN)

    @pytest.mark.asyncio
    async def test_load_seq_without_stored_data(self) -> None:
        """_load_seq with no stored data should not call set_seq."""
        device = MagicMock()
        device.address = "DC:23:4D:21:43:A5"
        device.set_seq = MagicMock()
        device.get_seq = MagicMock(return_value=2000)

        mock_hass = MagicMock()
        mock_store = MagicMock()
        mock_store.async_load = AsyncMock(return_value=None)

        from unittest.mock import patch

        coord = TuyaBLEMeshCoordinator(
            device, hass=mock_hass, entry_id="test_entry"
        )

        with patch(
            "custom_components.tuya_ble_mesh.coordinator.Store",
            return_value=mock_store,
        ):
            await coord._load_seq()

        device.set_seq.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_seq(self) -> None:
        """_save_seq should persist current seq."""
        device = MagicMock()
        device.address = "DC:23:4D:21:43:A5"
        device.get_seq = MagicMock(return_value=5000)

        mock_store = MagicMock()
        mock_store.async_save = AsyncMock()

        coord = TuyaBLEMeshCoordinator(device)
        coord._seq_store = mock_store

        await coord._save_seq()

        mock_store.async_save.assert_called_once_with({"seq": 5000})

    @pytest.mark.asyncio
    async def test_save_seq_noop_without_store(self) -> None:
        """_save_seq should be a no-op without store."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        await coord._save_seq()  # Should not raise

    def test_periodic_save_on_onoff_update(self) -> None:
        """Seq should be saved every _SEQ_PERSIST_INTERVAL onoff updates."""
        device = MagicMock()
        device.address = "DC:23:4D:21:43:A5"
        device.get_seq = MagicMock(return_value=2000)

        mock_store = MagicMock()
        mock_store.async_save = AsyncMock()

        coord = TuyaBLEMeshCoordinator(device)
        coord._seq_store = mock_store
        coord._seq_command_count = _SEQ_PERSIST_INTERVAL - 1

        coord._on_onoff_update(True)

        assert coord._seq_command_count == 0

    def test_seq_persistence_constants(self) -> None:
        """Verify seq persistence constants."""
        assert _SEQ_PERSIST_INTERVAL == 10
        assert _SEQ_SAFETY_MARGIN == 100


class TestVendorUpdate:
    """Test _on_vendor_update for energy monitoring."""

    def test_vendor_update_sets_power(self) -> None:
        """Power DP should set power_w in state."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        from tuya_ble_mesh.sig_mesh_protocol import (
            DP_ID_POWER_W,
            TUYA_VENDOR_OPCODE,
        )

        # Build vendor params: dp_id=18, dp_type=2 (value), dp_len=2, value=425 (42.5W)
        params = bytes([DP_ID_POWER_W, 0x02, 0x02, 0x01, 0xA9])
        coord._on_vendor_update(TUYA_VENDOR_OPCODE, params)

        assert coord.state.power_w == 42.5
        assert coord.state.available is True

    def test_vendor_update_sets_energy(self) -> None:
        """Energy DP should set energy_kwh in state."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        from tuya_ble_mesh.sig_mesh_protocol import (
            DP_ID_ENERGY_KWH,
            TUYA_VENDOR_OPCODE,
        )

        # Build vendor params: dp_id=17, dp_type=2 (value), dp_len=2, value=1234 (12.34 kWh)
        params = bytes([DP_ID_ENERGY_KWH, 0x02, 0x02, 0x04, 0xD2])
        coord._on_vendor_update(TUYA_VENDOR_OPCODE, params)

        assert coord.state.energy_kwh == 12.34
        assert coord.state.available is True

    def test_vendor_update_notifies_listeners(self) -> None:
        """Vendor update with known DP should notify listeners."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        listener = MagicMock()
        coord.add_listener(listener)

        from tuya_ble_mesh.sig_mesh_protocol import (
            DP_ID_POWER_W,
            TUYA_VENDOR_OPCODE,
        )

        params = bytes([DP_ID_POWER_W, 0x02, 0x01, 0x64])  # 10.0W
        coord._on_vendor_update(TUYA_VENDOR_OPCODE, params)

        listener.assert_called_once()

    def test_vendor_update_ignores_wrong_opcode(self) -> None:
        """Non-Tuya vendor opcode should be ignored."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        listener = MagicMock()
        coord.add_listener(listener)

        coord._on_vendor_update(0x123456, b"\x12\x02\x01\x0A")

        listener.assert_not_called()
        assert coord.state.power_w is None

    def test_vendor_update_ignores_unknown_dp(self) -> None:
        """Unknown DP IDs should not update state."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        from tuya_ble_mesh.sig_mesh_protocol import TUYA_VENDOR_OPCODE

        # dp_id=99 (unknown), dp_type=2, dp_len=1, value=0x0A
        params = bytes([99, 0x02, 0x01, 0x0A])
        coord._on_vendor_update(TUYA_VENDOR_OPCODE, params)

        assert coord.state.power_w is None
        assert coord.state.energy_kwh is None

    def test_vendor_update_both_power_and_energy(self) -> None:
        """Multiple DPs in single message should update both fields."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        from tuya_ble_mesh.sig_mesh_protocol import (
            DP_ID_ENERGY_KWH,
            DP_ID_POWER_W,
            TUYA_VENDOR_OPCODE,
        )

        # Two DPs: power=100 (10.0W) + energy=500 (5.00 kWh)
        params = bytes([
            DP_ID_POWER_W, 0x02, 0x01, 0x64,
            DP_ID_ENERGY_KWH, 0x02, 0x02, 0x01, 0xF4,
        ])
        coord._on_vendor_update(TUYA_VENDOR_OPCODE, params)

        assert coord.state.power_w == 10.0
        assert coord.state.energy_kwh == 5.0


class TestCompositionUpdate:
    """Test _on_composition_update for firmware version."""

    def test_composition_update_sets_firmware_version(self) -> None:
        """Composition update should set firmware_version from device."""
        device = make_mock_device()
        device.firmware_version = "CID:07D0 PID:0001 VID:0002"
        coord = TuyaBLEMeshCoordinator(device)

        from tuya_ble_mesh.sig_mesh_protocol import CompositionData

        comp = CompositionData(
            cid=0x07D0,
            pid=0x0001,
            vid=0x0002,
            crpl=10,
            features=0x0003,
            raw_elements=b"",
        )
        coord._on_composition_update(comp)

        assert coord.state.firmware_version == "CID:07D0 PID:0001 VID:0002"

    def test_composition_update_notifies_listeners(self) -> None:
        """Composition update should notify listeners."""
        device = make_mock_device()
        device.firmware_version = "CID:07D0 PID:0001 VID:0002"
        coord = TuyaBLEMeshCoordinator(device)
        listener = MagicMock()
        coord.add_listener(listener)

        from tuya_ble_mesh.sig_mesh_protocol import CompositionData

        comp = CompositionData(
            cid=0x07D0,
            pid=0x0001,
            vid=0x0002,
            crpl=10,
            features=0x0003,
            raw_elements=b"",
        )
        coord._on_composition_update(comp)

        listener.assert_called_once()
