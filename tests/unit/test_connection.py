"""Unit tests for BLEConnection transport class."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from tuya_ble_mesh.connection import (
    _INITIAL_BACKOFF,
    _MAX_BACKOFF,
    KEEP_ALIVE_INTERVAL,
    BLEConnection,
    ConnectionState,
)
from tuya_ble_mesh.const import TELINK_VENDOR_ID
from tuya_ble_mesh.exceptions import ConnectionError, DisconnectedError

MAC = "DC:23:4D:21:43:A5"
MESH_NAME = b"out_of_mesh"
MESH_PASS = b"123456"
SESSION_KEY = b"\x00" * 16


def _make_conn() -> BLEConnection:
    """Create a BLEConnection with default test parameters."""
    return BLEConnection(MAC, MESH_NAME, MESH_PASS)


def _make_ready_conn() -> tuple[BLEConnection, AsyncMock]:
    """Create a BLEConnection in READY state with mock client."""
    conn = _make_conn()
    client = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.write_gatt_char = AsyncMock()
    conn._client = client
    conn._session_key = bytearray(SESSION_KEY)
    conn._state = ConnectionState.READY
    return conn, client


# --- ConnectionState enum ---


class TestConnectionState:
    """Test connection state enum values."""

    def test_all_states(self) -> None:
        states = [s.value for s in ConnectionState]
        assert "disconnected" in states
        assert "connecting" in states
        assert "pairing" in states
        assert "ready" in states
        assert "disconnecting" in states

    def test_state_count(self) -> None:
        assert len(ConnectionState) == 7


# --- Construction ---


class TestConstruction:
    """Test BLEConnection initialization."""

    def test_initial_state(self) -> None:
        conn = _make_conn()
        assert conn.state == ConnectionState.DISCONNECTED

    def test_address_uppercased(self) -> None:
        conn = BLEConnection("dc:23:4d:21:43:a5", MESH_NAME, MESH_PASS)
        assert conn.address == MAC

    def test_not_ready_initially(self) -> None:
        conn = _make_conn()
        assert conn.is_ready is False

    def test_no_session_key_initially(self) -> None:
        conn = _make_conn()
        assert conn.session_key is None

    def test_default_vendor_id(self) -> None:
        conn = _make_conn()
        assert conn._vendor_id == TELINK_VENDOR_ID

    def test_custom_vendor_id(self) -> None:
        awox_vendor = bytes([0x60, 0x01])
        conn = BLEConnection(MAC, MESH_NAME, MESH_PASS, vendor_id=awox_vendor)
        assert conn._vendor_id == awox_vendor


# --- Connect ---


class TestConnect:
    """Test connection lifecycle."""

    @pytest.mark.asyncio
    async def test_connect_success(self) -> None:
        conn = _make_conn()
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.read_gatt_char = AsyncMock(return_value=b"1.0.0")
        mock_ble_device = MagicMock()

        with (
            patch(
                "tuya_ble_mesh.connection.BleakScanner.find_device_by_address",
                return_value=mock_ble_device,
            ),
            patch("tuya_ble_mesh.connection.BleakClient", return_value=mock_client),
            patch("tuya_ble_mesh.connection.provision", return_value=SESSION_KEY),
        ):
            await conn.connect()

        assert conn.state == ConnectionState.READY
        assert conn.is_ready is True
        assert conn.session_key is not None
        mock_client.connect.assert_called_once()

        # Clean up keep-alive
        await conn._stop_keep_alive()

    @pytest.mark.asyncio
    async def test_connect_already_ready(self) -> None:
        conn, _ = _make_ready_conn()
        await conn.connect()  # Should be a no-op

    @pytest.mark.asyncio
    async def test_connect_device_not_found(self) -> None:
        conn = _make_conn()
        with (
            patch(
                "tuya_ble_mesh.connection.BleakScanner.find_device_by_address",
                return_value=None,
            ),
            pytest.raises(ConnectionError, match="not found"),
        ):
            await conn.connect()

        assert conn.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_connect_provision_failure(self) -> None:
        conn = _make_conn()
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()
        mock_client.read_gatt_char = AsyncMock(return_value=b"1.0.0")
        mock_ble_device = MagicMock()

        with (
            patch(
                "tuya_ble_mesh.connection.BleakScanner.find_device_by_address",
                return_value=mock_ble_device,
            ),
            patch("tuya_ble_mesh.connection.BleakClient", return_value=mock_client),
            patch(
                "tuya_ble_mesh.connection.provision",
                side_effect=OSError("pair failed"),
            ),
            pytest.raises(ConnectionError, match="Provisioning failed"),
        ):
            await conn.connect()

        assert conn.state == ConnectionState.DISCONNECTED
        assert conn.session_key is None
        mock_client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_uses_establish_connection(self) -> None:
        """Verify bleak-retry-connector establish_connection is used."""
        conn = _make_conn()

        mock_client = AsyncMock()
        mock_client.read_gatt_char = AsyncMock(return_value=b"1.0.0")

        async def mock_scan(
            *args: object,
            **kwargs: object,
        ) -> MagicMock:
            return MagicMock()

        async def mock_establish(*args: object, **kwargs: object) -> AsyncMock:
            return mock_client

        with (
            patch(
                "tuya_ble_mesh.connection.BleakScanner.find_device_by_address",
                side_effect=mock_scan,
            ),
            patch(
                "tuya_ble_mesh.connection.establish_connection",
                side_effect=mock_establish,
            ) as mock_est,
            patch("tuya_ble_mesh.connection.provision", return_value=SESSION_KEY),
        ):
            await conn.connect()

        mock_est.assert_called_once()
        await conn._stop_keep_alive()

    @pytest.mark.asyncio
    async def test_cancelled_error_retried(self) -> None:
        """CancelledError on first attempt should retry and succeed."""
        conn = _make_conn()
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(
            side_effect=[asyncio.CancelledError(), None],
        )
        mock_client.read_gatt_char = AsyncMock(return_value=b"1.0.0")
        mock_ble_device = MagicMock()

        with (
            patch.object(conn, "_clear_bluez_device", new_callable=AsyncMock),
            patch(
                "tuya_ble_mesh.connection.BleakScanner.find_device_by_address",
                return_value=mock_ble_device,
            ),
            patch("tuya_ble_mesh.connection.BleakClient", return_value=mock_client),
            patch("tuya_ble_mesh.connection.provision", return_value=SESSION_KEY),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await conn.connect()

        assert conn.state == ConnectionState.READY
        await conn._stop_keep_alive()

    @pytest.mark.asyncio
    async def test_all_retries_cancelled_raises_connection_error(self) -> None:
        """All retries hitting CancelledError should raise ConnectionError."""
        conn = _make_conn()
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(side_effect=asyncio.CancelledError())
        mock_client.read_gatt_char = AsyncMock(return_value=b"1.0.0")
        mock_ble_device = MagicMock()

        with (
            patch.object(conn, "_clear_bluez_device", new_callable=AsyncMock),
            patch(
                "tuya_ble_mesh.connection.BleakScanner.find_device_by_address",
                return_value=mock_ble_device,
            ),
            patch("tuya_ble_mesh.connection.BleakClient", return_value=mock_client),
            patch("asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ConnectionError, match="Failed to connect"),
        ):
            await conn.connect(max_retries=3)

        assert conn.state == ConnectionState.DISCONNECTED


# --- Disconnect ---


class TestDisconnect:
    """Test disconnection."""

    @pytest.mark.asyncio
    async def test_disconnect(self) -> None:
        conn, client = _make_ready_conn()
        await conn.disconnect()

        assert conn.state == ConnectionState.DISCONNECTED
        assert conn.session_key is None
        client.disconnect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_when_already_disconnected(self) -> None:
        conn = _make_conn()
        await conn.disconnect()  # Should not raise

    @pytest.mark.asyncio
    async def test_session_key_zeroed_on_disconnect(self) -> None:
        conn, _ = _make_ready_conn()
        key_ref = conn._session_key
        assert key_ref is not None

        await conn.disconnect()

        # Verify original bytearray was zero-filled
        assert all(b == 0 for b in key_ref)
        assert conn._session_key is None


# --- Write command ---


class TestWriteCommand:
    """Test write_command method."""

    @pytest.mark.asyncio
    async def test_write_success(self) -> None:
        conn, client = _make_ready_conn()
        packet = b"\x00" * 20
        await conn.write_command(packet)
        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_write_not_ready_raises(self) -> None:
        conn = _make_conn()
        with pytest.raises(DisconnectedError, match="Not connected"):
            await conn.write_command(b"\x00" * 20)

    @pytest.mark.asyncio
    async def test_write_failure_triggers_disconnect(self) -> None:
        conn, client = _make_ready_conn()
        client.write_gatt_char = AsyncMock(side_effect=OSError("BLE write failed"))

        with pytest.raises(ConnectionError, match="Write failed"):
            await conn.write_command(b"\x00" * 20)

        assert conn.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_write_failure_calls_disconnect_callbacks(self) -> None:
        conn, client = _make_ready_conn()
        client.write_gatt_char = AsyncMock(side_effect=OSError("fail"))
        callback = MagicMock()
        conn.register_disconnect_callback(callback)

        with pytest.raises(ConnectionError):
            await conn.write_command(b"\x00" * 20)

        callback.assert_called_once()


# --- Disconnect callbacks ---


class TestDisconnectCallbacks:
    """Test disconnect callback registration."""

    def test_register_and_unregister(self) -> None:
        conn = _make_conn()
        cb = MagicMock()
        conn.register_disconnect_callback(cb)
        assert cb in conn._disconnect_callbacks
        conn.unregister_disconnect_callback(cb)
        assert cb not in conn._disconnect_callbacks

    @pytest.mark.asyncio
    async def test_callback_error_isolated(self) -> None:
        conn, client = _make_ready_conn()
        client.write_gatt_char = AsyncMock(side_effect=OSError("fail"))

        def bad_cb() -> None:
            msg = "boom"
            raise RuntimeError(msg)

        good_cb = MagicMock()
        conn.register_disconnect_callback(bad_cb)
        conn.register_disconnect_callback(good_cb)

        with pytest.raises(ConnectionError):
            await conn.write_command(b"\x00" * 20)

        # Second callback still called despite first raising
        good_cb.assert_called_once()


# --- Sequence counter ---


class TestSequence:
    """Test sequence counter."""

    @pytest.mark.asyncio
    async def test_increments(self) -> None:
        conn = _make_conn()
        s1 = await conn.next_sequence()
        s2 = await conn.next_sequence()
        assert s2 == s1 + 1

    @pytest.mark.asyncio
    async def test_wraps_at_24_bits(self) -> None:
        conn = _make_conn()
        conn._sequence = 0xFFFFFF
        assert await conn.next_sequence() == 0xFFFFFF
        assert await conn.next_sequence() == 0


# --- Keep-alive ---


class TestKeepAlive:
    """Test keep-alive mechanism."""

    @pytest.mark.asyncio
    async def test_keep_alive_started_on_connect(self) -> None:
        conn = _make_conn()
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.read_gatt_char = AsyncMock(side_effect=OSError("Not available"))
        mock_client.read_gatt_char = AsyncMock(return_value=b"1.0.0")
        mock_ble_device = MagicMock()

        with (
            patch(
                "tuya_ble_mesh.connection.BleakScanner.find_device_by_address",
                return_value=mock_ble_device,
            ),
            patch("tuya_ble_mesh.connection.BleakClient", return_value=mock_client),
            patch("tuya_ble_mesh.connection.provision", return_value=SESSION_KEY),
        ):
            await conn.connect()

        assert conn._keep_alive_task is not None
        await conn._stop_keep_alive()

    @pytest.mark.asyncio
    async def test_keep_alive_stopped_on_disconnect(self) -> None:
        conn, _ = _make_ready_conn()
        await conn._start_keep_alive()
        assert conn._keep_alive_task is not None

        await conn.disconnect()
        assert conn._keep_alive_task is None

    @pytest.mark.asyncio
    async def test_keep_alive_sends_status_query(self) -> None:
        conn, client = _make_ready_conn()

        # Directly call _send_keep_alive
        await conn._send_keep_alive()

        client.write_gatt_char.assert_called_once()

    @pytest.mark.asyncio
    async def test_keep_alive_failure_triggers_disconnect(self) -> None:
        conn, client = _make_ready_conn()
        client.write_gatt_char = AsyncMock(side_effect=OSError("timeout"))

        await conn._send_keep_alive()

        assert conn.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_keep_alive_uses_custom_vendor_id(self) -> None:
        """Keep-alive packets use the configured vendor_id."""
        awox_vendor = bytes([0x60, 0x01])
        conn = BLEConnection(MAC, MESH_NAME, MESH_PASS, vendor_id=awox_vendor)
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()
        conn._client = client
        conn._session_key = bytearray(SESSION_KEY)
        conn._state = ConnectionState.READY

        with patch(
            "tuya_ble_mesh.connection.encode_command_packet",
            return_value=b"\x00" * 20,
        ) as mock_encode:
            await conn._send_keep_alive()
            mock_encode.assert_called_once()
            _, kwargs = mock_encode.call_args
            assert kwargs["vendor_id"] == awox_vendor

    def test_keep_alive_interval(self) -> None:
        assert KEEP_ALIVE_INTERVAL == 30.0


# --- Backoff calculation ---


class TestBackoff:
    """Test backoff calculation."""

    def test_doubles(self) -> None:
        next_val = BLEConnection.calculate_backoff(5.0)
        assert 10.0 <= next_val <= 12.0  # 10 + up to 20% jitter

    def test_capped_at_max(self) -> None:
        next_val = BLEConnection.calculate_backoff(200.0)
        assert next_val <= _MAX_BACKOFF * 1.2 + 1  # max + jitter

    def test_initial_backoff(self) -> None:
        assert _INITIAL_BACKOFF == 5.0

    def test_max_backoff(self) -> None:
        assert _MAX_BACKOFF == 300.0


# --- _start_notify_safe ---


class TestStartNotifySafe:
    """Tests for the _start_notify_safe notification subscription helper."""

    @pytest.mark.asyncio
    async def test_returns_true_when_start_notify_succeeds(self) -> None:
        conn, client = _make_ready_conn()
        client.start_notify = AsyncMock()
        handler = MagicMock()
        conn.set_notification_handler(handler)

        result = await conn._start_notify_safe()

        assert result is True
        assert conn.notify_active is True
        client.start_notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_false_when_start_notify_fails(self) -> None:
        conn, client = _make_ready_conn()
        client.start_notify = AsyncMock(side_effect=EOFError("BlueZ crash"))
        handler = MagicMock()
        conn.set_notification_handler(handler)

        result = await conn._start_notify_safe()

        assert result is False
        assert conn.notify_active is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_handler(self) -> None:
        conn, _ = _make_ready_conn()
        # No handler registered

        result = await conn._start_notify_safe()

        assert result is False
        assert conn.notify_active is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_client(self) -> None:
        conn = _make_conn()
        conn.set_notification_handler(MagicMock())
        # No client

        result = await conn._start_notify_safe()

        assert result is False

    @pytest.mark.asyncio
    async def test_notify_active_reset_on_cleanup(self) -> None:
        conn, client = _make_ready_conn()
        client.start_notify = AsyncMock()
        client.disconnect = AsyncMock()
        conn.set_notification_handler(MagicMock())

        await conn._start_notify_safe()
        assert conn.notify_active is True

        await conn._cleanup()
        assert conn.notify_active is False

    @pytest.mark.asyncio
    async def test_start_notify_called_with_correct_char_and_handler(self) -> None:
        from tuya_ble_mesh.const import TELINK_CHAR_STATUS

        conn, client = _make_ready_conn()
        client.start_notify = AsyncMock()
        handler = MagicMock()
        conn.set_notification_handler(handler)

        await conn._start_notify_safe()

        client.start_notify.assert_called_once_with(TELINK_CHAR_STATUS, handler)

    @pytest.mark.asyncio
    async def test_notify_active_false_on_generic_exception(self) -> None:
        """Any exception from start_notify must result in poll-only mode."""
        conn, client = _make_ready_conn()
        client.start_notify = AsyncMock(side_effect=RuntimeError("unexpected"))
        conn.set_notification_handler(MagicMock())

        result = await conn._start_notify_safe()

        assert result is False
        assert conn.notify_active is False
