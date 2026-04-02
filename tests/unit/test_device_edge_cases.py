"""Unit tests for device.py — edge cases not covered by test_device.py.

Covers:
  firmware_version / rssi: 168-169, 190-191
  _handle_notification: 234-236 (CryptoError), 249 (CancelledError re-raise)
  _on_disconnect: 260-261 (CancelledError), 262-263 (Exception swallowed)
  _send_now: 341-342 (DisconnectedError), 369-389 (retry / final raise)
  wait_for_status: 416-417, 428 (callback fires, result returned)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

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

from tuya_ble_mesh.connection import ConnectionState
from tuya_ble_mesh.device import MeshDevice
from tuya_ble_mesh.exceptions import (
    CryptoError,
    DisconnectedError,
    MeshConnectionError,
)
from tuya_ble_mesh.protocol import StatusResponse


def _status(**kwargs: int) -> StatusResponse:
    """Build a minimal StatusResponse with all fields set."""
    defaults = {
        "mesh_id": 0,
        "mode": 1,
        "white_brightness": 50,
        "white_temp": 100,
        "color_brightness": 0,
        "red": 0,
        "green": 0,
        "blue": 0,
    }
    defaults.update(kwargs)
    return StatusResponse(**defaults)


MAC = "DC:23:4D:21:43:A5"
SESSION_KEY = b"\x00" * 16


def _make_device() -> MeshDevice:
    return MeshDevice(MAC, b"out_of_mesh", b"123456")


def _make_ready_device() -> tuple[MeshDevice, MagicMock]:
    """Return device in READY state with mock BLE client."""
    device = _make_device()
    conn = device._conn
    conn._state = ConnectionState.READY
    conn._session_key = bytearray(SESSION_KEY)
    client = AsyncMock()
    client.write_gatt_char = AsyncMock()
    client.disconnect = AsyncMock()
    client.read_gatt_char = AsyncMock(side_effect=OSError("not available"))
    conn._client = client
    return device, client


# ── Properties ────────────────────────────────────────────────────────────────


class TestProperties:
    """Lines 168-169, 190-191: firmware_version and rssi with non-None values."""

    def test_firmware_version_returns_string_when_set(self) -> None:
        device, _ = _make_ready_device()
        device._conn._firmware_version = "1.2.3"
        assert device.firmware_version == "1.2.3"

    def test_firmware_version_returns_none_when_unset(self) -> None:
        device, _ = _make_ready_device()
        device._conn._firmware_version = None
        assert device.firmware_version is None

    def test_rssi_returns_int_when_client_has_rssi(self) -> None:
        device, client = _make_ready_device()
        client.rssi = -60
        assert device.rssi == -60

    def test_rssi_returns_none_when_client_lacks_rssi(self) -> None:
        device, client = _make_ready_device()
        # Remove rssi attribute so getattr returns None
        del client.rssi
        assert device.rssi is None


# ── _handle_notification ──────────────────────────────────────────────────────


class TestHandleNotification:
    """Lines 234-236, 249: error handling in _handle_notification."""

    def test_crypto_error_is_swallowed(self) -> None:
        """Lines 234-236: CryptoError during decryption → silently return."""
        device, _ = _make_ready_device()
        with patch(
            "tuya_ble_mesh.device.decrypt_notification",
            side_effect=CryptoError("bad MAC"),
        ):
            # Must not raise
            device._handle_notification(Mock(), bytearray(b"\x00" * 20))

    def test_cancelled_error_in_callback_is_reraised(self) -> None:
        """Line 249: asyncio.CancelledError from status callback propagates."""
        device, _ = _make_ready_device()
        mock_status = _status()

        def raise_cancelled(status: object) -> None:
            raise asyncio.CancelledError()

        device.register_status_callback(raise_cancelled)
        with (
            patch("tuya_ble_mesh.device.decrypt_notification", return_value=b"\x00" * 16),
            patch("tuya_ble_mesh.device.decode_status", return_value=mock_status),
            pytest.raises(asyncio.CancelledError),
        ):
            device._handle_notification(Mock(), bytearray(b"\x00" * 20))

    def test_exception_in_callback_is_swallowed(self) -> None:
        """Existing line 250-251: generic Exception from callback is swallowed."""
        device, _ = _make_ready_device()
        mock_status = _status()

        def raise_error(status: object) -> None:
            raise RuntimeError("callback failed")

        device.register_status_callback(raise_error)
        with (
            patch("tuya_ble_mesh.device.decrypt_notification", return_value=b"\x00" * 16),
            patch("tuya_ble_mesh.device.decode_status", return_value=mock_status),
        ):
            # Must not raise
            device._handle_notification(Mock(), bytearray(b"\x00" * 20))


# ── _on_disconnect ────────────────────────────────────────────────────────────


class TestOnDisconnect:
    """Lines 260-263: callback error handling in _on_disconnect."""

    def test_cancelled_error_in_callback_is_reraised(self) -> None:
        """Lines 260-261: asyncio.CancelledError from disconnect callback propagates."""
        device = _make_device()

        def raise_cancelled() -> None:
            raise asyncio.CancelledError()

        device.register_disconnect_callback(raise_cancelled)
        with pytest.raises(asyncio.CancelledError):
            device._on_disconnect()

    def test_exception_in_callback_is_swallowed(self) -> None:
        """Lines 262-263: generic Exception from disconnect callback is swallowed."""
        device = _make_device()

        def raise_error() -> None:
            raise RuntimeError("disconnect callback failed")

        device.register_disconnect_callback(raise_error)
        # Must not raise
        device._on_disconnect()


# ── _send_now ─────────────────────────────────────────────────────────────────


class TestSendNow:
    """Lines 341-342, 369-389: _send_now error handling and retry."""

    @pytest.mark.asyncio
    async def test_raises_disconnected_when_no_session_key(self) -> None:
        """Lines 341-342: session_key is None → DisconnectedError immediately."""
        device, _ = _make_ready_device()
        device._conn._session_key = None  # Simulate disconnected state
        with pytest.raises(DisconnectedError, match="Not connected"):
            await device._send_now(0x01, b"", 0x00B0, max_retries=1)

    @pytest.mark.asyncio
    async def test_mesh_connection_error_retried_then_raised(self) -> None:
        """Lines 369-384, 386-387: MeshConnectionError retried, eventually re-raised."""
        device, _ = _make_ready_device()
        err = MeshConnectionError("write failed")
        device._conn.write_command = AsyncMock(side_effect=err)
        device._conn.next_sequence = AsyncMock(return_value=1)

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("tuya_ble_mesh.device.encode_command_packet", return_value=b"\x00" * 16),
            pytest.raises(MeshConnectionError),
        ):
            await device._send_now(0x01, b"", 0x00B0, max_retries=2)

        assert device._conn.write_command.call_count == 2

    @pytest.mark.asyncio
    async def test_oserror_retried_then_raised(self) -> None:
        """OSError also triggers retry path (lines 371, 386-387)."""
        device, _ = _make_ready_device()
        device._conn.write_command = AsyncMock(side_effect=OSError("pipe broken"))
        device._conn.next_sequence = AsyncMock(return_value=1)

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch("tuya_ble_mesh.device.encode_command_packet", return_value=b"\x00" * 16),
            pytest.raises(OSError),
        ):
            await device._send_now(0x01, b"", 0x00B0, max_retries=1)

    @pytest.mark.asyncio
    async def test_disconnected_error_not_retried(self) -> None:
        """Line 369-370: DisconnectedError during write → immediate re-raise."""
        device, _ = _make_ready_device()
        device._conn.write_command = AsyncMock(side_effect=DisconnectedError("gone"))
        device._conn.next_sequence = AsyncMock(return_value=1)

        with (
            patch("tuya_ble_mesh.device.encode_command_packet", return_value=b"\x00" * 16),
            pytest.raises(DisconnectedError),
        ):
            await device._send_now(0x01, b"", 0x00B0, max_retries=3)

        # Only one attempt — no retry on DisconnectedError
        assert device._conn.write_command.call_count == 1


# ── wait_for_status ───────────────────────────────────────────────────────────


class TestWaitForStatus:
    """Lines 416-417, 428: wait_for_status returns the first notification received."""

    @pytest.mark.asyncio
    async def test_returns_first_status_received(self) -> None:
        """Lines 416-417, 428: callback fires event and result is returned."""
        device = _make_device()
        expected = _status(mode=7)

        async def fire_callback() -> None:
            await asyncio.sleep(0)  # yield so wait_for_status registers callback first
            # Directly invoke all registered status callbacks
            for cb in list(device._status_callbacks):
                cb(expected)

        fire_task = asyncio.create_task(fire_callback())
        result = await device.wait_for_status(timeout=2.0)
        await fire_task

        assert result is expected
