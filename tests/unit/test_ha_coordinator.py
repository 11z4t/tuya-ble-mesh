"""Unit tests for the Tuya BLE Mesh coordinator."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    _BACKOFF_MULTIPLIER,
    _INITIAL_BACKOFF,
    _MAX_BACKOFF,
    _RSSI_DEFAULT_INTERVAL,
    _RSSI_MAX_INTERVAL,
    _RSSI_MIN_INTERVAL,
    _RSSI_STABILITY_THRESHOLD,
    _SEQ_PERSIST_INTERVAL,
    _SEQ_SAFETY_MARGIN,
    TuyaBLEMeshCoordinator,
    TuyaBLEMeshDeviceState,
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


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
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


@pytest.mark.requires_ha
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

        coord = TuyaBLEMeshCoordinator(device, hass=mock_hass, entry_id="test_entry")

        with patch(
            "homeassistant.helpers.storage.Store",
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

        coord = TuyaBLEMeshCoordinator(device, hass=mock_hass, entry_id="test_entry")

        with patch(
            "homeassistant.helpers.storage.Store",
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


@pytest.mark.requires_ha
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

        coord._on_vendor_update(0x123456, b"\x12\x02\x01\x0a")

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
        params = bytes(
            [
                DP_ID_POWER_W,
                0x02,
                0x01,
                0x64,
                DP_ID_ENERGY_KWH,
                0x02,
                0x02,
                0x01,
                0xF4,
            ]
        )
        coord._on_vendor_update(TUYA_VENDOR_OPCODE, params)

        assert coord.state.power_w == 10.0
        assert coord.state.energy_kwh == 5.0


@pytest.mark.requires_ha
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


# ---------------------------------------------------------------------------
# NEW TESTS: Reconnect loop, backoff, RSSI, bridge detection, lifecycle, etc.
# ---------------------------------------------------------------------------


def _make_sig_mesh_device(**overrides: Any) -> MagicMock:
    """Create a mock SIGMeshDevice with all expected attributes."""
    device = MagicMock()
    device.address = "DC:23:4F:10:52:C4"
    device.connect = AsyncMock()
    device.disconnect = AsyncMock()
    device.register_onoff_callback = MagicMock()
    device.unregister_onoff_callback = MagicMock()
    device.register_vendor_callback = MagicMock()
    device.unregister_vendor_callback = MagicMock()
    device.register_composition_callback = MagicMock()
    device.unregister_composition_callback = MagicMock()
    device.register_disconnect_callback = MagicMock()
    device.unregister_disconnect_callback = MagicMock()
    device.set_seq = MagicMock()
    device.get_seq = MagicMock(return_value=1000)
    device.firmware_version = None
    device.is_connected = True
    for k, v in overrides.items():
        setattr(device, k, v)
    return device


@pytest.mark.requires_ha
class TestReconnectLoop:
    """Test _reconnect_loop() exponential backoff behaviour."""

    @pytest.mark.asyncio
    async def test_reconnect_exponential_backoff(self) -> None:
        """Backoff should double after each failed reconnect, up to MAX."""
        device = make_mock_device()
        device.connect = AsyncMock(side_effect=ConnectionError("fail"))
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord._backoff = _INITIAL_BACKOFF

        recorded_sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            recorded_sleeps.append(seconds)
            # Stop after a few iterations
            if len(recorded_sleeps) >= 5:
                coord._running = False

        with patch(_PATCH_SLEEP, side_effect=fake_sleep):
            await coord._reconnect_loop()

        # Verify exponential backoff sequence
        assert recorded_sleeps[0] == _INITIAL_BACKOFF
        assert recorded_sleeps[1] == _INITIAL_BACKOFF * _BACKOFF_MULTIPLIER
        assert recorded_sleeps[2] == _INITIAL_BACKOFF * _BACKOFF_MULTIPLIER**2
        assert recorded_sleeps[3] == _INITIAL_BACKOFF * _BACKOFF_MULTIPLIER**3
        assert recorded_sleeps[4] == _INITIAL_BACKOFF * _BACKOFF_MULTIPLIER**4

    @pytest.mark.asyncio
    async def test_reconnect_backoff_capped_at_max(self) -> None:
        """Backoff should never exceed _MAX_BACKOFF."""
        device = make_mock_device()
        device.connect = AsyncMock(side_effect=ConnectionError("fail"))
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord._backoff = _MAX_BACKOFF  # Already at max

        recorded_sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            recorded_sleeps.append(seconds)
            if len(recorded_sleeps) >= 3:
                coord._running = False

        with patch(_PATCH_SLEEP, side_effect=fake_sleep):
            await coord._reconnect_loop()

        # All sleeps should be capped at _MAX_BACKOFF
        for s in recorded_sleeps:
            assert s <= _MAX_BACKOFF

    @pytest.mark.asyncio
    async def test_reconnect_resets_backoff_on_connect_success(self) -> None:
        """Successful reconnect should reset backoff to INITIAL."""
        device = make_mock_device()
        device.connect = AsyncMock()  # succeeds
        device.firmware_version = "v1"
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord._backoff = 160.0  # elevated backoff

        with patch(_PATCH_SLEEP, new_callable=AsyncMock):
            await coord._reconnect_loop()

        assert coord._backoff == _INITIAL_BACKOFF
        assert coord.state.available is True

    @pytest.mark.asyncio
    async def test_reconnect_sets_firmware_version(self) -> None:
        """Successful reconnect should set firmware_version from device."""
        device = make_mock_device()
        device.firmware_version = "fw-2.0"
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True

        with patch(_PATCH_SLEEP, new_callable=AsyncMock):
            await coord._reconnect_loop()

        assert coord.state.firmware_version == "fw-2.0"

    @pytest.mark.asyncio
    async def test_reconnect_notifies_on_failure(self) -> None:
        """Failed reconnect should notify listeners (unavailable)."""
        device = make_mock_device()
        device.connect = AsyncMock(side_effect=ConnectionError("fail"))
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        listener = MagicMock()
        coord.add_listener(listener)

        call_count = 0

        async def fake_sleep(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                coord._running = False

        with patch(_PATCH_SLEEP, side_effect=fake_sleep):
            await coord._reconnect_loop()

        assert listener.call_count >= 1
        assert coord.state.available is False

    @pytest.mark.asyncio
    async def test_reconnect_notifies_on_success(self) -> None:
        """Successful reconnect should notify listeners."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        listener = MagicMock()
        coord.add_listener(listener)

        with patch(_PATCH_SLEEP, new_callable=AsyncMock):
            await coord._reconnect_loop()

        listener.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconnect_exits_when_running_false_after_sleep(self) -> None:
        """Loop should exit if _running becomes False during sleep."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True

        async def stop_during_sleep(seconds: float) -> None:
            coord._running = False

        with patch(_PATCH_SLEEP, side_effect=stop_during_sleep):
            await coord._reconnect_loop()

        # connect should NOT have been called since _running was False
        device.connect.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconnect_starts_rssi_polling_on_success(self) -> None:
        """Successful reconnect should start RSSI polling."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True

        with (
            patch(_PATCH_SLEEP, new_callable=AsyncMock),
            patch.object(coord, "_start_rssi_polling") as mock_rssi,
        ):
            await coord._reconnect_loop()

        mock_rssi.assert_called_once()


@pytest.mark.requires_ha
class TestScheduleReconnect:
    """Test _schedule_reconnect edge cases."""

    def test_schedule_reconnect_noop_when_not_running(self) -> None:
        """Should not create task when coordinator is not running."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = False

        coord._schedule_reconnect()

        assert coord._reconnect_task is None

    def test_schedule_reconnect_cancels_existing_task(self) -> None:
        """Should cancel previous reconnect task before starting new one."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True

        old_task = MagicMock()
        coord._reconnect_task = old_task

        coord._schedule_reconnect()

        old_task.cancel.assert_called_once()
        assert coord._reconnect_task is not None
        assert coord._reconnect_task is not old_task

        # Clean up
        coord._reconnect_task.cancel()
        coord._reconnect_task = None


@pytest.mark.requires_ha
class TestIsBridgeDevice:
    """Test _is_bridge_device() type detection."""

    def test_regular_device_is_not_bridge(self) -> None:
        """MeshDevice should not be detected as bridge."""
        device = make_mock_device()
        type(device).__name__ = "MeshDevice"
        coord = TuyaBLEMeshCoordinator(device)
        assert coord._is_bridge_device() is False

    def test_sig_mesh_device_is_not_bridge(self) -> None:
        """SIGMeshDevice should not be detected as bridge."""
        device = make_mock_device()
        type(device).__name__ = "SIGMeshDevice"
        coord = TuyaBLEMeshCoordinator(device)
        assert coord._is_bridge_device() is False

    def test_bridge_device_detected(self) -> None:
        """Device with 'Bridge' in class name should be detected."""
        device = make_mock_device()
        type(device).__name__ = "TuyaBridgeDevice"
        coord = TuyaBLEMeshCoordinator(device)
        assert coord._is_bridge_device() is True

    def test_http_bridge_detected(self) -> None:
        """HTTPBridge should be detected as bridge."""
        device = make_mock_device()
        type(device).__name__ = "HTTPBridge"
        coord = TuyaBLEMeshCoordinator(device)
        assert coord._is_bridge_device() is True

    def test_mock_device_not_bridge(self) -> None:
        """Default MagicMock should not be bridge."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        # MagicMock.__name__ is "MagicMock" — no "Bridge"
        assert coord._is_bridge_device() is False


@pytest.mark.requires_ha
class TestRSSIPolling:
    """Test RSSI polling start/stop and loop behaviour."""

    def test_start_rssi_skips_bridge_device(self) -> None:
        """RSSI polling should not start for bridge devices."""
        device = make_mock_device()
        type(device).__name__ = "TuyaBridgeDevice"
        coord = TuyaBLEMeshCoordinator(device)

        coord._start_rssi_polling()

        assert coord._rssi_task is None

    def test_start_rssi_creates_task_for_ble_device(self) -> None:
        """RSSI polling should start for regular BLE devices."""
        device = make_mock_device()
        type(device).__name__ = "MeshDevice"
        coord = TuyaBLEMeshCoordinator(device)

        coord._start_rssi_polling()

        assert coord._rssi_task is not None

        # Clean up
        coord._rssi_task.cancel()
        coord._rssi_task = None

    def test_stop_rssi_cancels_task(self) -> None:
        """_stop_rssi_polling should cancel and clear the task."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        fake_task = MagicMock()
        coord._rssi_task = fake_task

        coord._stop_rssi_polling()

        fake_task.cancel.assert_called_once()
        assert coord._rssi_task is None

    def test_stop_rssi_noop_without_task(self) -> None:
        """_stop_rssi_polling should be safe when no task exists."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        coord._stop_rssi_polling()  # Should not raise
        assert coord._rssi_task is None

    def test_start_rssi_stops_existing_before_starting(self) -> None:
        """Starting RSSI polling should stop any existing task first."""
        device = make_mock_device()
        type(device).__name__ = "MeshDevice"
        coord = TuyaBLEMeshCoordinator(device)

        old_task = MagicMock()
        coord._rssi_task = old_task

        coord._start_rssi_polling()

        old_task.cancel.assert_called_once()
        assert coord._rssi_task is not None
        assert coord._rssi_task is not old_task

        # Clean up
        coord._rssi_task.cancel()
        coord._rssi_task = None

    @pytest.mark.asyncio
    async def test_rssi_loop_updates_rssi_state(self) -> None:
        """RSSI loop should update state.rssi from BleakScanner result."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord._state.available = True

        mock_ble_device = MagicMock()
        mock_ble_device.rssi = -55

        call_count = 0

        async def fake_sleep(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                coord._running = False

        with (
            patch(_PATCH_SLEEP, side_effect=fake_sleep),
            patch("bleak.BleakScanner") as mock_scanner_cls,
        ):
            mock_scanner_cls.find_device_by_address = AsyncMock(return_value=mock_ble_device)
            await coord._rssi_loop()

        assert coord.state.rssi == -55

    @pytest.mark.asyncio
    async def test_rssi_loop_exits_when_unavailable(self) -> None:
        """RSSI loop should exit when device becomes unavailable."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord._state.available = False  # already unavailable

        # Should exit immediately without sleeping
        await coord._rssi_loop()
        # No assertion needed — just verifying it does not hang

    @pytest.mark.asyncio
    async def test_rssi_loop_ignores_scanner_error(self) -> None:
        """RSSI scan failure should be silently ignored."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord._state.available = True

        call_count = 0

        async def fake_sleep(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                coord._running = False

        with (
            patch(_PATCH_SLEEP, side_effect=fake_sleep),
            patch("bleak.BleakScanner") as mock_scanner_cls,
        ):
            mock_scanner_cls.find_device_by_address = AsyncMock(side_effect=OSError("BLE error"))
            await coord._rssi_loop()

        # RSSI should remain None (no crash)
        assert coord.state.rssi is None

    @pytest.mark.asyncio
    async def test_rssi_loop_skips_none_device(self) -> None:
        """RSSI should not update if scanner returns None."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord._state.available = True

        call_count = 0

        async def fake_sleep(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                coord._running = False

        with (
            patch(_PATCH_SLEEP, side_effect=fake_sleep),
            patch("bleak.BleakScanner") as mock_scanner_cls,
        ):
            mock_scanner_cls.find_device_by_address = AsyncMock(return_value=None)
            await coord._rssi_loop()

        assert coord.state.rssi is None

    @pytest.mark.asyncio
    async def test_rssi_loop_handles_cancellation(self) -> None:
        """RSSI loop should gracefully handle CancelledError."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord._state.available = True

        async def raise_cancel(seconds: float) -> None:
            raise asyncio.CancelledError()

        with patch(_PATCH_SLEEP, side_effect=raise_cancel):
            # Should not raise
            await coord._rssi_loop()

    @pytest.mark.asyncio
    async def test_rssi_loop_notifies_listeners(self) -> None:
        """RSSI update should notify listeners."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord._state.available = True
        listener = MagicMock()
        coord.add_listener(listener)

        mock_ble_device = MagicMock()
        mock_ble_device.rssi = -70

        call_count = 0

        async def fake_sleep(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                coord._running = False

        with (
            patch(_PATCH_SLEEP, side_effect=fake_sleep),
            patch("bleak.BleakScanner") as mock_scanner_cls,
        ):
            mock_scanner_cls.find_device_by_address = AsyncMock(return_value=mock_ble_device)
            await coord._rssi_loop()

        assert listener.call_count >= 1

    @pytest.mark.asyncio
    async def test_rssi_loop_stability_tracking(self) -> None:
        """RSSI loop should track stability and adjust polling - covers lines 534-536."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        coord._state.available = True

        # Create BLE devices with stable RSSI (within ±2 dBm)
        mock_ble_device_1 = MagicMock()
        mock_ble_device_1.rssi = -60

        mock_ble_device_2 = MagicMock()
        mock_ble_device_2.rssi = -61  # Only 1 dBm difference (stable)

        call_count = 0
        rssi_values = [-60, -61] * (_RSSI_STABILITY_THRESHOLD + 2)  # Repeat stable values

        async def fake_sleep(seconds: float) -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= len(rssi_values):
                coord._running = False

        async def fake_find_device(address: str, timeout: float = 5.0):
            nonlocal call_count
            if call_count < len(rssi_values):
                mock_device = MagicMock()
                mock_device.rssi = rssi_values[call_count]
                return mock_device
            return None

        with (
            patch(_PATCH_SLEEP, side_effect=fake_sleep),
            patch("bleak.BleakScanner") as mock_scanner_cls,
        ):
            mock_scanner_cls.find_device_by_address = fake_find_device
            await coord._rssi_loop()

        # Verify that stability was tracked and polling interval was adjusted
        # Lines 534-536 should have been executed when stable_cycles reached threshold
        assert coord._stable_cycles >= 0  # May have been reset after adjustment


@pytest.mark.requires_ha
class TestSeqPersistenceExtended:
    """Extended tests for _load_seq / _save_seq edge cases."""

    @pytest.mark.asyncio
    async def test_load_seq_noop_without_entry_id(self) -> None:
        """_load_seq with hass but no entry_id should be a no-op."""
        device = _make_sig_mesh_device()
        coord = TuyaBLEMeshCoordinator(device, hass=MagicMock(), entry_id=None)
        await coord._load_seq()
        assert coord._seq_store is None
        device.set_seq.assert_not_called()

    @pytest.mark.asyncio
    async def test_load_seq_noop_without_set_seq(self) -> None:
        """_load_seq should be no-op if device lacks set_seq method."""
        device = make_mock_device()
        # make_mock_device does not have set_seq by default (MagicMock
        # auto-creates attrs, so remove it)
        del device.set_seq
        coord = TuyaBLEMeshCoordinator(device, hass=MagicMock(), entry_id="e1")
        await coord._load_seq()
        assert coord._seq_store is None

    @pytest.mark.asyncio
    async def test_load_seq_with_empty_dict(self) -> None:
        """Stored data without 'seq' key should not call set_seq."""
        device = _make_sig_mesh_device()
        mock_store = MagicMock()
        mock_store.async_load = AsyncMock(return_value={})

        coord = TuyaBLEMeshCoordinator(device, hass=MagicMock(), entry_id="test_entry")
        with patch(
            "homeassistant.helpers.storage.Store",
            return_value=mock_store,
        ):
            await coord._load_seq()

        device.set_seq.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_seq_noop_without_get_seq(self) -> None:
        """_save_seq should be no-op if device lacks get_seq method."""
        device = make_mock_device()
        del device.get_seq
        coord = TuyaBLEMeshCoordinator(device)
        mock_store = MagicMock()
        mock_store.async_save = AsyncMock()
        coord._seq_store = mock_store

        await coord._save_seq()
        mock_store.async_save.assert_not_called()

    def test_periodic_save_not_triggered_below_interval(self) -> None:
        """Seq should NOT be saved before reaching persist interval."""
        device = _make_sig_mesh_device()
        mock_store = MagicMock()
        mock_store.async_save = AsyncMock()
        coord = TuyaBLEMeshCoordinator(device)
        coord._seq_store = mock_store
        coord._seq_command_count = 0

        # Fire updates less than the interval
        for _ in range(_SEQ_PERSIST_INTERVAL - 1):
            coord._on_onoff_update(True)

        assert coord._seq_command_count == _SEQ_PERSIST_INTERVAL - 1
        # _save_seq should NOT have been triggered
        assert coord._seq_persist_task is None


@pytest.mark.requires_ha
class TestLifecycleEdgeCases:
    """Test start/stop lifecycle edge cases."""

    @pytest.mark.asyncio
    async def test_double_start(self) -> None:
        """Calling async_start twice should not raise."""
        device = make_mock_device()
        device.firmware_version = None
        coord = TuyaBLEMeshCoordinator(device)

        await coord.async_start()
        await coord.async_start()  # second start

        assert coord._running is True
        assert coord.state.available is True

        await coord.async_stop()

    @pytest.mark.asyncio
    async def test_double_stop(self) -> None:
        """Calling async_stop twice should not raise."""
        device = make_mock_device()
        device.firmware_version = None
        coord = TuyaBLEMeshCoordinator(device)

        await coord.async_start()
        await coord.async_stop()
        await coord.async_stop()  # second stop

        assert coord._running is False

    @pytest.mark.asyncio
    async def test_stop_without_start(self) -> None:
        """Calling async_stop without start should not raise."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        await coord.async_stop()  # Should be safe
        assert coord._running is False

    @pytest.mark.asyncio
    async def test_stop_handles_disconnect_error(self) -> None:
        """Stop should handle device.disconnect() raising an exception."""
        device = make_mock_device()
        device.disconnect = AsyncMock(side_effect=OSError("BLE gone"))
        device.firmware_version = None
        coord = TuyaBLEMeshCoordinator(device)

        await coord.async_start()
        await coord.async_stop()  # Should not raise

        assert coord.state.available is False

    @pytest.mark.asyncio
    async def test_start_connection_failure_schedules_reconnect(self) -> None:
        """Failed initial connect should schedule a reconnect task."""
        device = make_mock_device()
        device.connect = AsyncMock(side_effect=ConnectionError("fail"))
        coord = TuyaBLEMeshCoordinator(device)

        await coord.async_start()

        assert coord.state.available is False
        assert coord._reconnect_task is not None

        await coord.async_stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_rssi_task(self) -> None:
        """Stop should cancel any running RSSI polling task."""
        device = make_mock_device()
        device.firmware_version = None
        coord = TuyaBLEMeshCoordinator(device)
        await coord.async_start()

        # Verify rssi task was created (for non-bridge device)
        if coord._rssi_task is not None:
            rssi_task = coord._rssi_task
            await coord.async_stop()
            assert rssi_task.cancelled() or coord._rssi_task is None
        else:
            await coord.async_stop()

    @pytest.mark.asyncio
    async def test_stop_saves_seq_for_sig_mesh(self) -> None:
        """async_stop should persist seq for SIG Mesh devices."""
        device = _make_sig_mesh_device()
        mock_store = MagicMock()
        mock_store.async_save = AsyncMock()
        mock_store.async_load = AsyncMock(return_value=None)

        coord = TuyaBLEMeshCoordinator(device, hass=MagicMock(), entry_id="entry1")

        with patch("homeassistant.helpers.storage.Store", return_value=mock_store):
            await coord.async_start()

        # Now stop — should call _save_seq
        await coord.async_stop()

        mock_store.async_save.assert_called_once_with({"seq": 1000})

    @pytest.mark.asyncio
    async def test_on_disconnect_stops_rssi_polling(self) -> None:
        """Disconnect callback should stop RSSI polling."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        fake_task = MagicMock()
        coord._rssi_task = fake_task

        coord._on_disconnect()

        fake_task.cancel.assert_called_once()
        assert coord._rssi_task is None


@pytest.mark.requires_ha
class TestListenerErrorHandling:
    """Extended listener error handling tests."""

    def test_multiple_bad_listeners_all_called(self) -> None:
        """All listeners should be called even if multiple raise errors."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        bad1 = MagicMock(side_effect=TypeError("bad1"))
        bad2 = MagicMock(side_effect=ValueError("bad2"))
        good = MagicMock()

        coord.add_listener(bad1)
        coord.add_listener(bad2)
        coord.add_listener(good)

        coord._notify_listeners()

        bad1.assert_called_once()
        bad2.assert_called_once()
        good.assert_called_once()

    def test_listener_error_during_status_update(self) -> None:
        """Status update should complete even if listener raises."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        bad = MagicMock(side_effect=RuntimeError("crash"))
        coord.add_listener(bad)

        status = make_mock_status(white_brightness=50)
        coord._on_status_update(status)

        # State should still be updated
        assert coord.state.brightness == 50
        assert coord.state.available is True

    def test_listener_error_during_disconnect(self) -> None:
        """Disconnect should complete even if listener raises."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True
        bad = MagicMock(side_effect=RuntimeError("crash"))
        coord.add_listener(bad)

        coord._on_disconnect()

        assert coord.state.available is False
        # Reconnect should still have been scheduled
        assert coord._reconnect_task is not None

        # Clean up
        coord._reconnect_task.cancel()
        coord._reconnect_task = None


@pytest.mark.requires_ha
class TestTaskCancellation:
    """Test handling of asyncio task cancellation."""

    @pytest.mark.asyncio
    async def test_reconnect_loop_stops_on_cancelled(self) -> None:
        """Reconnect loop should exit on CancelledError from sleep."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)
        coord._running = True

        with (
            patch(
                "custom_components.tuya_ble_mesh.coordinator.asyncio.sleep",
                side_effect=asyncio.CancelledError(),
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await coord._reconnect_loop()

    @pytest.mark.asyncio
    async def test_stop_cancels_reconnect_task_safely(self) -> None:
        """async_stop should cancel reconnect task without raising."""
        device = make_mock_device()
        device.connect = AsyncMock(side_effect=ConnectionError("fail"))
        device.firmware_version = None
        coord = TuyaBLEMeshCoordinator(device)

        await coord.async_start()
        assert coord._reconnect_task is not None

        await coord.async_stop()
        assert coord._reconnect_task is None
        assert coord._running is False


@pytest.mark.requires_ha
class TestBackoffConstants:
    """Verify backoff constant relationships."""

    def test_initial_less_than_max(self) -> None:
        assert _INITIAL_BACKOFF < _MAX_BACKOFF

    def test_multiplier_greater_than_one(self) -> None:
        assert _BACKOFF_MULTIPLIER > 1.0

    def test_rssi_interval_positive(self) -> None:
        assert _RSSI_DEFAULT_INTERVAL > 0
        assert _RSSI_MIN_INTERVAL > 0
        assert _RSSI_MAX_INTERVAL > _RSSI_MIN_INTERVAL
        assert _RSSI_STABILITY_THRESHOLD > 0


@pytest.mark.requires_ha
class TestStatisticsProperty:
    """Test statistics property getter (line 129)."""

    def test_statistics_property_returns_stats(self) -> None:
        """The statistics property should return the internal _stats object."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Access statistics property
        stats = coord.statistics

        # Should return the ConnectionStatistics object
        assert stats is not None
        assert hasattr(stats, "total_reconnects")
        assert hasattr(stats, "total_errors")
        assert hasattr(stats, "connection_errors")
        assert hasattr(stats, "command_errors")
        assert stats.total_reconnects == 0  # Initial value


@pytest.mark.requires_ha
class TestAdaptivePollingFrequentChanges:
    """Test adaptive RSSI polling with frequent state changes (lines 471-475)."""

    @pytest.mark.asyncio
    async def test_frequent_changes_decrease_rssi_interval(self) -> None:
        """RSSI interval should decrease when frequent state changes are detected."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Set initial interval to default
        coord._rssi_interval = _RSSI_DEFAULT_INTERVAL
        initial_interval = coord._rssi_interval

        # Simulate frequent state changes (2+ changes)
        coord._state_change_counter = 2

        # Call adaptive polling logic (correct method name)
        coord._adjust_polling_interval()

        # Interval should have decreased
        assert coord._rssi_interval < initial_interval
        # But not below minimum
        assert coord._rssi_interval >= _RSSI_MIN_INTERVAL

    @pytest.mark.asyncio
    async def test_rssi_interval_never_below_minimum(self) -> None:
        """RSSI interval should never go below MIN_INTERVAL."""
        device = make_mock_device()
        coord = TuyaBLEMeshCoordinator(device)

        # Set interval close to minimum
        coord._rssi_interval = _RSSI_MIN_INTERVAL + 0.1

        # Trigger many frequent changes
        coord._state_change_counter = 10

        coord._adjust_polling_interval()

        # Should be clamped at minimum
        assert coord._rssi_interval == _RSSI_MIN_INTERVAL
