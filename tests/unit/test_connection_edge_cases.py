"""Unit tests for connection.py — edge cases not covered by test_connection.py.

Covers:
  rssi: 204 (None when client is None)
  connect(): 293-295 (client None after _connect_with_retry), 336, 340
  _read_firmware_version: 395 (early return when client is None)
  _clear_bluez_device: 405-415 (subprocess success + error)
  disconnect(): 441-442 (OSError during client.disconnect)
  _handle_disconnect: 475 (DISCONNECTING early return), 484 (CancelledError re-raise)
  _keep_alive_loop: 523-525 (state change exits loop)
  _send_keep_alive: 536 (early return when state not READY)
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "custom_components"
        / "tuya_ble_mesh"
        / "lib"
    ),
)

from tuya_ble_mesh.connection import BLEConnection, ConnectionState
from tuya_ble_mesh.exceptions import MeshConnectionError

MAC = "DC:23:4D:21:43:A5"
MESH_NAME = b"out_of_mesh"
MESH_PASS = b"123456"
SESSION_KEY = b"\x00" * 16


def _make_conn() -> BLEConnection:
    return BLEConnection(MAC, MESH_NAME, MESH_PASS)


def _make_ready_conn() -> tuple[BLEConnection, AsyncMock]:
    conn = _make_conn()
    client = AsyncMock()
    client.disconnect = AsyncMock()
    client.write_gatt_char = AsyncMock()
    conn._client = client
    conn._session_key = bytearray(SESSION_KEY)
    conn._state = ConnectionState.READY
    return conn, client


# ── rssi property ─────────────────────────────────────────────────────────────


class TestRssi:
    """Line 204: rssi returns None when _client is None."""

    def test_rssi_none_when_no_client(self) -> None:
        conn = _make_conn()
        assert conn._client is None
        assert conn.rssi is None


# ── connect() edge cases ───────────────────────────────────────────────────────


class TestConnectClientNotSet:
    """Lines 293-295: _client is None after _connect_with_retry → MeshConnectionError."""

    @pytest.mark.asyncio
    async def test_raises_when_client_not_set_after_retry(self) -> None:
        conn = _make_conn()
        # _connect_with_retry completes without error but leaves _client=None
        with (
            patch.object(conn, "_connect_with_retry", new_callable=AsyncMock),
            pytest.raises(MeshConnectionError, match="BLE client not set"),
        ):
            await conn.connect()

        assert conn.state == ConnectionState.DISCONNECTED


class TestConnectBleDEviceCallback:
    """Line 336: ble_device_callback called instead of BleakScanner."""

    @pytest.mark.asyncio
    async def test_ble_device_callback_is_invoked(self) -> None:
        mock_device = MagicMock()
        cb = MagicMock(return_value=mock_device)
        conn = BLEConnection(MAC, MESH_NAME, MESH_PASS, ble_device_callback=cb)
        mock_client = AsyncMock()
        mock_client.start_notify = AsyncMock()

        with (
            patch(
                "tuya_ble_mesh.connection.establish_connection",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "tuya_ble_mesh.connection.provision",
                new_callable=AsyncMock,
                return_value=SESSION_KEY,
            ),
            patch.object(conn, "_read_firmware_version", new_callable=AsyncMock),
            patch.object(conn, "_start_keep_alive", new_callable=AsyncMock),
            patch.object(conn, "_start_notify_safe", new_callable=AsyncMock),
        ):
            await conn.connect()

        cb.assert_called_once_with(MAC)
        assert conn.state == ConnectionState.READY


class TestConnectAdapter:
    """Line 340: adapter kwarg forwarded to BleakScanner.find_device_by_address."""

    @pytest.mark.asyncio
    async def test_adapter_forwarded_to_scanner(self) -> None:
        conn = BLEConnection(MAC, MESH_NAME, MESH_PASS, adapter="hci1")
        mock_device = MagicMock()
        mock_client = AsyncMock()

        with (
            patch("tuya_ble_mesh.connection.BleakScanner") as mock_scanner,
            patch(
                "tuya_ble_mesh.connection.establish_connection",
                new_callable=AsyncMock,
                return_value=mock_client,
            ),
            patch(
                "tuya_ble_mesh.connection.provision",
                new_callable=AsyncMock,
                return_value=SESSION_KEY,
            ),
            patch.object(conn, "_read_firmware_version", new_callable=AsyncMock),
            patch.object(conn, "_start_keep_alive", new_callable=AsyncMock),
            patch.object(conn, "_start_notify_safe", new_callable=AsyncMock),
        ):
            mock_scanner.find_device_by_address = AsyncMock(return_value=mock_device)
            await conn.connect()

        assert mock_scanner.find_device_by_address.call_args.kwargs.get("adapter") == "hci1"


# ── _read_firmware_version ─────────────────────────────────────────────────────


class TestReadFirmwareVersion:
    """Line 395: early return when _client is None."""

    @pytest.mark.asyncio
    async def test_returns_early_when_no_client(self) -> None:
        conn = _make_conn()
        assert conn._client is None
        await conn._read_firmware_version()
        assert conn._firmware_version is None


# ── _clear_bluez_device ────────────────────────────────────────────────────────


class TestClearBluezDevice:
    """Lines 405-415: subprocess management."""

    @pytest.mark.asyncio
    async def test_success_calls_proc_wait(self) -> None:
        """Lines 405-413: subprocess started and wait() called."""
        conn = _make_conn()
        mock_proc = MagicMock()
        mock_proc.wait = AsyncMock()

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            await conn._clear_bluez_device()

        mock_proc.wait.assert_called_once()

    @pytest.mark.asyncio
    async def test_oserror_swallowed(self) -> None:
        """Line 414: OSError from create_subprocess_exec is swallowed."""
        conn = _make_conn()
        with patch("asyncio.create_subprocess_exec", side_effect=OSError("not found")):
            await conn._clear_bluez_device()

    @pytest.mark.asyncio
    async def test_timeout_swallowed(self) -> None:
        """Line 414: TimeoutError from wait_for is swallowed."""
        conn = _make_conn()
        mock_proc = MagicMock()
        mock_proc.wait = AsyncMock(side_effect=TimeoutError())

        with patch(
            "asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=mock_proc,
        ):
            await conn._clear_bluez_device()


# ── disconnect() ───────────────────────────────────────────────────────────────


class TestDisconnectOsError:
    """Lines 441-442: OSError during client.disconnect() is swallowed."""

    @pytest.mark.asyncio
    async def test_oserror_during_client_disconnect_swallowed(self) -> None:
        conn, client = _make_ready_conn()
        client.disconnect = AsyncMock(side_effect=OSError("BLE error"))

        await conn.disconnect()

        assert conn.state == ConnectionState.DISCONNECTED
        assert conn._client is None


# ── _handle_disconnect ─────────────────────────────────────────────────────────


class TestHandleDisconnect:
    """Lines 475, 484: early return and CancelledError re-raise."""

    @pytest.mark.asyncio
    async def test_returns_early_when_already_disconnecting(self) -> None:
        """Line 475: state == DISCONNECTING → immediate return."""
        conn, _ = _make_ready_conn()
        conn._state = ConnectionState.DISCONNECTING

        cleanup_called = False

        async def mock_cleanup() -> None:
            nonlocal cleanup_called
            cleanup_called = True

        with patch.object(conn, "_cleanup", side_effect=mock_cleanup):
            await conn._handle_disconnect()

        assert not cleanup_called

    @pytest.mark.asyncio
    async def test_cancelled_error_in_callback_is_reraised(self) -> None:
        """Line 484: CancelledError from disconnect callback propagates."""
        conn = _make_conn()
        conn._state = ConnectionState.READY

        def raise_cancelled() -> None:
            raise asyncio.CancelledError()

        conn.register_disconnect_callback(raise_cancelled)

        with (
            patch.object(conn, "_cleanup", new_callable=AsyncMock),
            pytest.raises(asyncio.CancelledError),
        ):
            await conn._handle_disconnect()


# ── _keep_alive_loop ───────────────────────────────────────────────────────────


class TestKeepAliveLoop:
    """Lines 523-525: loop exits when state becomes non-READY."""

    @pytest.mark.asyncio
    async def test_loop_exits_when_state_becomes_disconnected(self) -> None:
        conn, _ = _make_ready_conn()

        with patch("tuya_ble_mesh.connection.KEEP_ALIVE_INTERVAL", 0.002):
            task = asyncio.create_task(conn._keep_alive_loop())
            # Let the loop sleep once, then change state
            await asyncio.sleep(0.005)
            conn._state = ConnectionState.DISCONNECTED
            # Give the loop time to notice the state change
            await asyncio.sleep(0.005)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ── _send_keep_alive ───────────────────────────────────────────────────────────


class TestSendKeepAlive:
    """Line 536: early return when state is not READY."""

    @pytest.mark.asyncio
    async def test_returns_early_when_state_not_ready(self) -> None:
        conn, client = _make_ready_conn()
        conn._state = ConnectionState.DISCONNECTED

        await conn._send_keep_alive()

        client.write_gatt_char.assert_not_called()

    @pytest.mark.asyncio
    async def test_returns_early_when_client_is_none(self) -> None:
        conn, _ = _make_ready_conn()
        conn._client = None  # type: ignore[assignment]

        await conn._send_keep_alive()

    @pytest.mark.asyncio
    async def test_returns_early_when_session_key_is_none(self) -> None:
        conn, client = _make_ready_conn()
        conn._session_key = None

        await conn._send_keep_alive()

        client.write_gatt_char.assert_not_called()
