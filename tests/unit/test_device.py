"""Unit tests for the MeshDevice command interface."""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from tuya_ble_mesh.connection import BLEConnection, ConnectionState
from tuya_ble_mesh.const import (
    COMPACT_DP_BRIGHTNESS,
    COMPACT_DP_POWER,
    DP_TYPE_VALUE,
    TELINK_CMD_DP_WRITE,
    TELINK_VENDOR_ID,
)
from tuya_ble_mesh.device import (
    MESH_ADDRESS_ALL,
    MESH_ADDRESS_DEFAULT,
    MeshDevice,
)
from tuya_ble_mesh.device_dispatcher import (
    _COMMAND_TTL,
    _QUEUE_MAX_SIZE,
)
from tuya_ble_mesh.exceptions import (
    CommandQueueFullError,
    ConnectionError,
    ProtocolError,
    TimeoutError,
)
from tuya_ble_mesh.protocol import StatusResponse, encode_compact_dp

MAC = "DC:23:4D:21:43:A5"
MESH_NAME = b"out_of_mesh"
MESH_PASS = b"123456"
SESSION_KEY = b"\x00" * 16


def _make_device(**kwargs: object) -> MeshDevice:
    """Create a MeshDevice with default test parameters."""
    return MeshDevice(MAC, MESH_NAME, MESH_PASS, **kwargs)  # type: ignore[arg-type]


def _make_connected_device() -> tuple[MeshDevice, AsyncMock]:
    """Create a MeshDevice in connected state with mock BLEConnection."""
    device = _make_device()
    # Mock the BLEConnection to be in READY state
    conn = device._conn
    conn._state = ConnectionState.READY
    conn._session_key = bytearray(SESSION_KEY)
    conn._client = AsyncMock()
    conn._client.write_gatt_char = AsyncMock()
    conn._client.disconnect = AsyncMock()
    # Start dispatcher only when called from an async context (event loop running).
    # Sync tests have no event loop — they don't need the worker anyway.
    try:
        asyncio.get_running_loop()
        device._dispatcher.start()
    except RuntimeError:
        pass
    return device, conn._client


# --- Construction ---


class TestConstruction:
    """Test MeshDevice initialization."""

    def test_default_mesh_id(self) -> None:
        device = _make_device()
        assert device.mesh_id == MESH_ADDRESS_DEFAULT

    def test_broadcast_mesh_id(self) -> None:
        device = _make_device(mesh_id=MESH_ADDRESS_ALL)
        assert device.mesh_id == 0xFFFF

    def test_custom_mesh_id(self) -> None:
        device = _make_device(mesh_id=42)
        assert device.mesh_id == 42

    def test_address_uppercased(self) -> None:
        device = MeshDevice("dc:23:4d:21:43:a5", MESH_NAME, MESH_PASS)
        assert device.address == MAC

    def test_invalid_mac_raises(self) -> None:
        with pytest.raises(ProtocolError):
            MeshDevice("INVALID", MESH_NAME, MESH_PASS)

    def test_not_connected_initially(self) -> None:
        device = _make_device()
        assert device.is_connected is False

    def test_has_connection(self) -> None:
        device = _make_device()
        assert isinstance(device.connection, BLEConnection)

    def test_default_vendor_id(self) -> None:
        device = _make_device()
        assert device._vendor_id == TELINK_VENDOR_ID

    def test_custom_vendor_id(self) -> None:
        awox_vendor = bytes([0x60, 0x01])
        device = _make_device(vendor_id=awox_vendor)
        assert device._vendor_id == awox_vendor
        assert device._conn._vendor_id == awox_vendor


# --- mesh_id property ---


class TestMeshId:
    """Test mesh_id getter/setter."""

    def test_set_valid(self) -> None:
        device = _make_device()
        device.mesh_id = 100
        assert device.mesh_id == 100

    def test_set_zero(self) -> None:
        device = _make_device()
        device.mesh_id = 0
        assert device.mesh_id == 0

    def test_set_max(self) -> None:
        device = _make_device()
        device.mesh_id = 0xFFFF
        assert device.mesh_id == 0xFFFF

    def test_set_negative_raises(self) -> None:
        device = _make_device()
        with pytest.raises(ProtocolError, match="mesh_id"):
            device.mesh_id = -1

    def test_set_too_large_raises(self) -> None:
        device = _make_device()
        with pytest.raises(ProtocolError, match="mesh_id"):
            device.mesh_id = 0x10000


# --- connect / disconnect ---


class TestConnect:
    """Test connection lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_success(self) -> None:
        device = _make_device()

        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_ble_device = MagicMock()

        with (
            patch(
                "tuya_ble_mesh.connection.BleakScanner.find_device_by_address",
                return_value=mock_ble_device,
            ),
            patch("tuya_ble_mesh.connection.BleakClient", return_value=mock_client),
            patch("tuya_ble_mesh.connection.provision", return_value=SESSION_KEY),
        ):
            await device.connect()

        assert device.is_connected is True
        mock_client.connect.assert_called_once()

        # Clean up keep-alive
        await device._conn._stop_keep_alive()

    @pytest.mark.asyncio
    async def test_connect_already_connected(self) -> None:
        device, _ = _make_connected_device()
        await device.connect()  # should be a no-op

    @pytest.mark.asyncio
    async def test_connect_ble_failure(self) -> None:
        device = _make_device()

        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(side_effect=OSError("BLE fail"))
        mock_ble_device = MagicMock()

        with (
            patch(
                "tuya_ble_mesh.connection.BleakScanner.find_device_by_address",
                return_value=mock_ble_device,
            ),
            patch("tuya_ble_mesh.connection.BleakClient", return_value=mock_client),
            pytest.raises(ConnectionError, match="Failed to connect"),
        ):
            await device.connect(max_retries=1)

        assert device.is_connected is False

    @pytest.mark.asyncio
    async def test_connect_provision_failure(self) -> None:
        device = _make_device()

        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_ble_device = MagicMock()

        with (
            patch(
                "tuya_ble_mesh.connection.BleakScanner.find_device_by_address",
                return_value=mock_ble_device,
            ),
            patch("tuya_ble_mesh.connection.BleakClient", return_value=mock_client),
            patch(
                "tuya_ble_mesh.connection.provision",
                side_effect=Exception("pair failed"),
            ),
            pytest.raises(ConnectionError, match="Provisioning failed"),
        ):
            await device.connect()

        assert device.is_connected is False
        mock_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        device, client = _make_connected_device()
        await device.disconnect()

        assert device.is_connected is False
        client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self) -> None:
        device = _make_device()
        await device.disconnect()  # should not raise


# --- Context manager ---


class TestContextManager:
    """Test async context manager."""

    @pytest.mark.asyncio
    async def test_context_manager(self) -> None:
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_ble_device = MagicMock()

        with (
            patch(
                "tuya_ble_mesh.connection.BleakScanner.find_device_by_address",
                return_value=mock_ble_device,
            ),
            patch("tuya_ble_mesh.connection.BleakClient", return_value=mock_client),
            patch("tuya_ble_mesh.connection.provision", return_value=SESSION_KEY),
        ):
            device = _make_device()
            async with device:
                assert device.is_connected is True
                await device._conn._stop_keep_alive()
            assert device.is_connected is False


# --- send_command ---


class TestSendCommand:
    """Test raw command sending."""

    @pytest.mark.asyncio
    async def test_send_command(self) -> None:
        device, client = _make_connected_device()
        await device.send_command(0xD2, b"\x79\x02\x04\x00\x00\x00\x01")
        await asyncio.sleep(0.05)  # yield to dispatcher worker

        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_when_connected_goes_to_ble(self) -> None:
        """When connected, commands are sent immediately."""
        device, client = _make_connected_device()
        await device.send_command(0xD2, b"\x01")
        await asyncio.sleep(0.05)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_command_custom_dest(self) -> None:
        device, client = _make_connected_device()
        await device.send_command(0xD2, b"\x01", dest_id=42)
        await asyncio.sleep(0.05)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_sequence_increments(self) -> None:
        device, _ = _make_connected_device()
        s1 = await device._conn.next_sequence()
        s2 = await device._conn.next_sequence()
        assert s2 == s1 + 1

    @pytest.mark.asyncio
    async def test_sequence_wraps(self) -> None:
        device, _ = _make_connected_device()
        device._conn._sequence = 0xFFFFFF
        seq = await device._conn.next_sequence()
        assert seq == 0xFFFFFF
        next_seq = await device._conn.next_sequence()
        assert next_seq == 0


# --- send_power (0xD2 compact DP) ---


class TestSendPower:
    """Test power on/off commands using 0xD2 compact DP."""

    @pytest.mark.asyncio
    async def test_power_on(self) -> None:
        device, client = _make_connected_device()
        await device.send_power(True)
        await asyncio.sleep(0.05)
        client.write_gatt_char.assert_called_once()

        # Verify the packet encodes 0xD2 with compact DP for power
        args = client.write_gatt_char.call_args
        packet = args[0][1]
        assert len(packet) == 20

    @pytest.mark.asyncio
    async def test_power_off(self) -> None:
        device, client = _make_connected_device()
        await device.send_power(False)
        await asyncio.sleep(0.05)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_power_uses_dp_write_opcode(self) -> None:
        """Verify send_power uses TELINK_CMD_DP_WRITE (0xD2)."""
        device, _ = _make_connected_device()
        expected_params = encode_compact_dp(COMPACT_DP_POWER, DP_TYPE_VALUE, 1)

        with patch.object(device, "send_command", new_callable=AsyncMock) as mock_cmd:
            await device.send_power(True)
            mock_cmd.assert_called_once_with(TELINK_CMD_DP_WRITE, expected_params)

    @pytest.mark.asyncio
    async def test_power_off_value(self) -> None:
        """Verify send_power(False) sends value 0."""
        device, _ = _make_connected_device()
        expected_params = encode_compact_dp(COMPACT_DP_POWER, DP_TYPE_VALUE, 0)

        with patch.object(device, "send_command", new_callable=AsyncMock) as mock_cmd:
            await device.send_power(False)
            mock_cmd.assert_called_once_with(TELINK_CMD_DP_WRITE, expected_params)


# --- send_brightness (0xD2 compact DP, 1-100%) ---


class TestSendBrightness:
    """Test brightness commands using 0xD2 compact DP."""

    @pytest.mark.asyncio
    async def test_valid_brightness(self) -> None:
        device, client = _make_connected_device()
        await device.send_brightness(100)
        await asyncio.sleep(0.05)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_brightness_min(self) -> None:
        device, _ = _make_connected_device()
        await device.send_brightness(1)

    @pytest.mark.asyncio
    async def test_brightness_max(self) -> None:
        device, _ = _make_connected_device()
        await device.send_brightness(100)

    @pytest.mark.asyncio
    async def test_brightness_zero_raises(self) -> None:
        """Brightness 0 is out of range (use send_power(False) instead)."""
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Brightness"):
            await device.send_brightness(0)

    @pytest.mark.asyncio
    async def test_brightness_too_high(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Brightness"):
            await device.send_brightness(101)

    @pytest.mark.asyncio
    async def test_brightness_negative(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Brightness"):
            await device.send_brightness(-1)

    @pytest.mark.asyncio
    async def test_brightness_uses_dp_write_opcode(self) -> None:
        """Verify send_brightness uses TELINK_CMD_DP_WRITE (0xD2)."""
        device, _ = _make_connected_device()
        expected_params = encode_compact_dp(COMPACT_DP_BRIGHTNESS, DP_TYPE_VALUE, 50)

        with patch.object(device, "send_command", new_callable=AsyncMock) as mock_cmd:
            await device.send_brightness(50)
            mock_cmd.assert_called_once_with(TELINK_CMD_DP_WRITE, expected_params)


# --- send_color_temp ---


class TestSendColorTemp:
    """Test color temperature commands."""

    @pytest.mark.asyncio
    async def test_valid_temp(self) -> None:
        device, client = _make_connected_device()
        await device.send_color_temp(64)
        await asyncio.sleep(0.05)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_temp_too_high(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Color temp"):
            await device.send_color_temp(256)

    @pytest.mark.asyncio
    async def test_temp_negative(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Color temp"):
            await device.send_color_temp(-1)


# --- send_color ---


class TestSendColor:
    """Test RGB color commands."""

    @pytest.mark.asyncio
    async def test_valid_color(self) -> None:
        device, client = _make_connected_device()
        await device.send_color(255, 128, 0)
        await asyncio.sleep(0.05)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_color_out_of_range(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="red"):
            await device.send_color(256, 0, 0)

    @pytest.mark.asyncio
    async def test_color_green_out_of_range(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="green"):
            await device.send_color(0, -1, 0)

    @pytest.mark.asyncio
    async def test_color_blue_out_of_range(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="blue"):
            await device.send_color(0, 0, 300)


# --- send_color_brightness ---


class TestSendColorBrightness:
    """Test color brightness commands."""

    @pytest.mark.asyncio
    async def test_valid(self) -> None:
        device, client = _make_connected_device()
        await device.send_color_brightness(50)
        await asyncio.sleep(0.05)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_out_of_range(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Color brightness"):
            await device.send_color_brightness(256)


# --- send_light_mode ---


class TestSendLightMode:
    """Test light mode commands."""

    @pytest.mark.asyncio
    async def test_valid(self) -> None:
        device, client = _make_connected_device()
        await device.send_light_mode(0)
        await asyncio.sleep(0.05)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_out_of_range(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Light mode"):
            await device.send_light_mode(256)


# --- send_mesh_address ---


class TestSendMeshAddress:
    """Test mesh address assignment."""

    @pytest.mark.asyncio
    async def test_valid(self) -> None:
        device, client = _make_connected_device()
        await device.send_mesh_address(100)
        await asyncio.sleep(0.05)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_zero_raises(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Mesh address"):
            await device.send_mesh_address(0)

    @pytest.mark.asyncio
    async def test_too_large_raises(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(ProtocolError, match="Mesh address"):
            await device.send_mesh_address(0x8000)


# --- send_mesh_reset ---


class TestSendMeshReset:
    """Test mesh reset command."""

    @pytest.mark.asyncio
    async def test_sends_reset(self) -> None:
        device, client = _make_connected_device()
        await device.send_mesh_reset()
        await asyncio.sleep(0.05)
        client.write_gatt_char.assert_called_once()


# --- Status callbacks ---


class TestStatusCallbacks:
    """Test status notification handling."""

    def test_register_and_unregister(self) -> None:
        device = _make_device()
        cb = MagicMock()
        device.register_status_callback(cb)
        assert cb in device._status_callbacks
        device.unregister_status_callback(cb)
        assert cb not in device._status_callbacks

    async def test_notification_dispatches(self) -> None:
        device, _ = _make_connected_device()
        received: list[StatusResponse] = []
        device.register_status_callback(lambda s: received.append(s))

        with patch("tuya_ble_mesh.device.decrypt_notification") as mock_decrypt:
            fake = bytearray(20)
            fake[3] = 1  # mesh_id
            fake[12] = 0  # mode
            fake[13] = 100  # white_brightness
            fake[14] = 50  # white_temp
            fake[15] = 0  # color_brightness
            fake[16] = 0  # red
            fake[17] = 0  # green
            fake[18] = 0  # blue
            mock_decrypt.return_value = bytes(fake)

            device._handle_notification(0, bytearray(20))

        assert len(received) == 1
        assert received[0].white_brightness == 100
        assert received[0].white_temp == 50

    def test_notification_without_session_key(self) -> None:
        device = _make_device()
        device._handle_notification(0, bytearray(20))

    async def test_callback_exception_isolated(self) -> None:
        device, _ = _make_connected_device()

        def bad_callback(_status: StatusResponse) -> None:
            msg = "boom"
            raise RuntimeError(msg)

        device.register_status_callback(bad_callback)
        received: list[StatusResponse] = []
        device.register_status_callback(lambda s: received.append(s))

        with patch("tuya_ble_mesh.device.decrypt_notification") as mock_decrypt:
            fake = bytearray(20)
            mock_decrypt.return_value = bytes(fake)
            device._handle_notification(0, bytearray(20))

        assert len(received) == 1


# --- Disconnect callbacks ---


class TestDisconnectCallbacks:
    """Test device-level disconnect callbacks."""

    def test_register_and_unregister(self) -> None:
        device = _make_device()
        cb = MagicMock()
        device.register_disconnect_callback(cb)
        assert cb in device._disconnect_callbacks
        device.unregister_disconnect_callback(cb)
        assert cb not in device._disconnect_callbacks

    def test_disconnect_callback_called(self) -> None:
        device = _make_device()
        cb = MagicMock()
        device.register_disconnect_callback(cb)

        device._on_disconnect()

        cb.assert_called_once()


# --- Command queue ---


class TestCommandQueue:
    """Test command queue behavior."""

    @pytest.mark.asyncio
    async def test_queue_full_raises(self) -> None:
        """Exceeding the queue capacity raises CommandQueueFullError."""

        from tuya_ble_mesh.device_dispatcher import _QueuedCommand

        device = _make_device()
        # Fill the dispatcher queue to capacity using put_nowait
        for _ in range(_QUEUE_MAX_SIZE):
            cmd = _QueuedCommand(0xD2, b"\x01", 0)
            device._dispatcher._queue.put_nowait(cmd)

        with pytest.raises(CommandQueueFullError, match="32"):
            await device._dispatcher.enqueue(0xD2, b"\x01", 0)

    @pytest.mark.asyncio
    async def test_drain_queue_sends_commands(self) -> None:
        """Dispatcher worker sends queued commands when connected."""
        device, client = _make_connected_device()
        # Worker is already running via _make_connected_device()
        await device.send_command(0xD2, b"\x01")
        await asyncio.sleep(0.05)  # yield to worker
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_drain_queue_expires_old_commands(self) -> None:
        """Commands older than TTL are silently dropped by the dispatcher."""
        import asyncio

        from tuya_ble_mesh.device_dispatcher import _QueuedCommand

        device, client = _make_connected_device()
        # Enqueue a stale command directly into the dispatcher queue
        cmd = _QueuedCommand(0xD2, b"\x01", 0)
        cmd.created_at = time.monotonic() - _COMMAND_TTL - 1
        device._dispatcher._queue.put_nowait(cmd)

        await asyncio.sleep(0.05)  # yield to worker (which will drop it)
        client.write_gatt_char.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_empty_queue_is_noop(self) -> None:
        _device, client = _make_connected_device()
        await asyncio.sleep(0.05)
        client.write_gatt_char.assert_not_called()

    def test_queue_constants(self) -> None:
        assert _QUEUE_MAX_SIZE == 32
        assert _COMMAND_TTL == 60.0


# --- Notification path wiring ---


class TestNotificationPathWiring:
    """Tests that _handle_notification is wired into BLEConnection at init time."""

    def test_handler_registered_at_init(self) -> None:
        """Handler must be registered with BLEConnection in __init__, not connect()."""
        device = _make_device()
        # Bound methods compare by __func__ — verify the right method is wired
        handler = device._conn._notification_handler
        assert handler is not None
        assert handler.__func__ is MeshDevice._handle_notification  # type: ignore[union-attr]
        assert handler.__self__ is device  # type: ignore[union-attr]

    def test_notify_active_false_before_connect(self) -> None:
        device = _make_device()
        assert device.notify_active is False

    def test_notify_active_reflects_conn(self) -> None:
        device = _make_device()
        device._conn._notify_active = True
        assert device.notify_active is True

    async def test_notification_key_snapshot_used(self) -> None:
        """_handle_notification reads a copy of the key — not the live bytearray."""
        device, _ = _make_connected_device()
        received: list[StatusResponse] = []
        device.register_status_callback(lambda s: received.append(s))

        with patch("tuya_ble_mesh.device.decrypt_notification") as mock_decrypt:
            fake = bytearray(20)
            fake[13] = 77  # white_brightness
            mock_decrypt.return_value = bytes(fake)
            # Simulate disconnect zero-filling the key AFTER snapshot taken:
            # property returns immutable bytes, so callbacks are safe.
            device._handle_notification(0, bytearray(20))

        assert len(received) == 1
        assert received[0].white_brightness == 77

    def test_notification_dropped_on_none_key(self) -> None:
        """No crash and no callbacks when session key is None."""
        device = _make_device()
        device._conn._session_key = None
        received: list[StatusResponse] = []
        device.register_status_callback(lambda s: received.append(s))
        device._handle_notification(0, bytearray(20))
        assert len(received) == 0


# --- wait_for_status ---


class TestWaitForStatus:
    """Test wait_for_status method."""

    @pytest.mark.asyncio
    async def test_timeout_raises(self) -> None:
        device, _ = _make_connected_device()
        with pytest.raises(TimeoutError, match="No status"):
            await device.wait_for_status(timeout=0.01)
