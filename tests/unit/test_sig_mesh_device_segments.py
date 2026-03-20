"""Unit tests for SIGMeshDeviceSegmentsMixin (via SIGMeshDevice).

Covers the notification dispatch and segment reassembly methods in
sig_mesh_device_segments.py that were not covered by test_sig_mesh_device.py:

- _log_notify_exception: cancelled task, task with exception, normal task
- _on_notify: keys None guard, task creation, RuntimeError (no loop)
- _process_notify: keys None, malformed PDU, decryption failures, seg routing
- _handle_segment: malformed header guard
- _complete_reassembly: buf None guard, opcode parse failure
- _dispatch_access_payload: opcode parse failure
- _dispatch_access_payload_unlocked: pending response matching, future-done guard
- Callback exception handling: onoff, vendor, composition, disconnect
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.exceptions import MalformedPacketError  # noqa: E402
from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice  # noqa: E402
from tuya_ble_mesh.sig_mesh_protocol import MeshKeys  # noqa: E402

_MOD = "tuya_ble_mesh.sig_mesh_device_segments"


def _make_device() -> SIGMeshDevice:
    """SIGMeshDevice with injected keys and mock client."""
    dev = SIGMeshDevice("DC:23:4F:10:52:C4", 0x0001, 0x0010, MagicMock())
    dev._keys = MeshKeys(
        "f7a2a44f8e8a8029064f173ddc1e2b00",  # pragma: allowlist secret
        "00112233445566778899aabbccddeeff",  # pragma: allowlist secret
        "3216d1509884b533248541792b877f98",  # pragma: allowlist secret
    )
    dev._client = MagicMock()
    return dev


# ---------------------------------------------------------------------------
# _log_notify_exception
# ---------------------------------------------------------------------------


class TestLogNotifyException:
    """Test _log_notify_exception handles task outcomes gracefully."""

    @pytest.mark.asyncio
    async def test_cancelled_task_returns_silently(self) -> None:
        dev = _make_device()
        task: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(10))
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # Must not raise
        dev._log_notify_exception(task)

    @pytest.mark.asyncio
    async def test_task_with_exception_logs_error(self) -> None:
        dev = _make_device()

        async def fail() -> None:
            raise RuntimeError("notify boom")

        task: asyncio.Task[None] = asyncio.create_task(fail())
        with contextlib.suppress(RuntimeError):
            await task
        # Must not raise; logs error internally
        dev._log_notify_exception(task)

    @pytest.mark.asyncio
    async def test_successful_task_does_nothing(self) -> None:
        async def ok() -> None:
            return

        dev = _make_device()
        task: asyncio.Task[None] = asyncio.create_task(ok())
        await task
        dev._log_notify_exception(task)  # Must not raise


# ---------------------------------------------------------------------------
# _on_notify
# ---------------------------------------------------------------------------


class TestOnNotify:
    """Test _on_notify GATT notification callback."""

    def test_no_op_when_keys_none(self) -> None:
        """_on_notify should return immediately when _keys is None."""
        dev = SIGMeshDevice("DC:23:4F:10:52:C4", 0x0001, 0x0010, MagicMock())
        # _keys is None — must not create a task
        dev._on_notify(MagicMock(), bytearray(b"\x00" * 20))
        assert len(dev._pending_notify_tasks) == 0

    @pytest.mark.asyncio
    async def test_creates_task_when_keys_loaded(self) -> None:
        """_on_notify should schedule _process_notify as an asyncio task."""
        dev = _make_device()

        # Patch _process_notify so it returns immediately without crashing
        async def fake_process(data: bytes) -> None:
            return

        dev._process_notify = fake_process  # type: ignore[assignment]

        # Call the synchronous GATT callback while an event loop is running
        dev._on_notify(MagicMock(), bytearray(b"\x00" * 20))

        # Task should have been queued into _pending_notify_tasks
        assert len(dev._pending_notify_tasks) == 1

        # Let the task complete
        await asyncio.sleep(0)

    def test_no_running_loop_logs_debug(self) -> None:
        """_on_notify called from a non-async context should log debug, not crash."""
        dev = _make_device()
        # No event loop is running in a sync test
        # asyncio.get_running_loop() raises RuntimeError — should be caught
        dev._on_notify(MagicMock(), bytearray(b"\x00" * 20))
        assert len(dev._pending_notify_tasks) == 0


# ---------------------------------------------------------------------------
# _process_notify
# ---------------------------------------------------------------------------


class TestProcessNotify:
    """Test _process_notify decryption and dispatch routing."""

    @pytest.mark.asyncio
    async def test_returns_early_when_keys_none(self) -> None:
        dev = SIGMeshDevice("DC:23:4F:10:52:C4", 0x0001, 0x0010, MagicMock())
        # _keys is None
        await dev._process_notify(b"\x00" * 20)  # Must not raise

    @pytest.mark.asyncio
    async def test_malformed_proxy_pdu_logs_debug(self) -> None:
        dev = _make_device()
        with patch(f"{_MOD}.parse_proxy_pdu", side_effect=MalformedPacketError("bad pdu")):
            await dev._process_notify(b"\x00" * 20)  # Must not raise or propagate

    @pytest.mark.asyncio
    async def test_network_pdu_decryption_failure_returns(self) -> None:
        """If decrypt_network_pdu returns None, should return silently."""
        dev = _make_device()
        mock_proxy = MagicMock()
        mock_proxy.payload = b"\x00" * 20
        with (
            patch(f"{_MOD}.parse_proxy_pdu", return_value=mock_proxy),
            patch(f"{_MOD}.decrypt_network_pdu", return_value=None),
        ):
            await dev._process_notify(b"\x00" * 29)

    @pytest.mark.asyncio
    async def test_access_payload_decryption_failure_returns(self) -> None:
        """If decrypt_access_payload returns None, should return silently."""
        dev = _make_device()
        mock_proxy = MagicMock()
        mock_net = MagicMock()
        mock_net.src = 0x0001
        mock_net.dst = 0x0010
        mock_net.seq = 1
        mock_net.transport_pdu = b"\x00" * 16
        with (
            patch(f"{_MOD}.parse_proxy_pdu", return_value=mock_proxy),
            patch(f"{_MOD}.decrypt_network_pdu", return_value=mock_net),
            patch(f"{_MOD}.decrypt_access_payload", return_value=None),
        ):
            await dev._process_notify(b"\x00" * 29)

    @pytest.mark.asyncio
    async def test_segmented_msg_routes_to_handle_segment(self) -> None:
        """Segmented access messages should be routed to _handle_segment."""
        dev = _make_device()
        mock_proxy = MagicMock()
        mock_net = MagicMock()
        mock_net.src = 0x0001
        mock_net.dst = 0x0010
        mock_net.seq = 1
        mock_net.transport_pdu = b"\x80" + b"\x00" * 15  # SEG=1 (MSB set in first byte)
        mock_access = MagicMock()
        mock_access.seg = True

        with (
            patch(f"{_MOD}.parse_proxy_pdu", return_value=mock_proxy),
            patch(f"{_MOD}.decrypt_network_pdu", return_value=mock_net),
            patch(f"{_MOD}.decrypt_access_payload", return_value=mock_access),
            patch.object(dev, "_handle_segment", new_callable=AsyncMock) as mock_handle,
        ):
            await dev._process_notify(b"\x00" * 29)

        mock_handle.assert_called_once_with(0x0001, 0x0010, mock_net.transport_pdu)

    @pytest.mark.asyncio
    async def test_none_access_payload_after_unseg_returns(self) -> None:
        """access_msg.access_payload is None for unsegmented → return silently."""
        dev = _make_device()
        mock_proxy = MagicMock()
        mock_net = MagicMock()
        mock_net.src = 0x0001
        mock_net.dst = 0x0010
        mock_net.seq = 1
        mock_net.transport_pdu = b"\x00" * 16
        mock_access = MagicMock()
        mock_access.seg = False
        mock_access.access_payload = None

        with (
            patch(f"{_MOD}.parse_proxy_pdu", return_value=mock_proxy),
            patch(f"{_MOD}.decrypt_network_pdu", return_value=mock_net),
            patch(f"{_MOD}.decrypt_access_payload", return_value=mock_access),
        ):
            await dev._process_notify(b"\x00" * 29)  # Must not raise


# ---------------------------------------------------------------------------
# _handle_segment — malformed header guard
# ---------------------------------------------------------------------------


class TestHandleSegment:
    """Test _handle_segment guards."""

    @pytest.mark.asyncio
    async def test_malformed_segment_header_returns_silently(self) -> None:
        dev = _make_device()
        with patch(f"{_MOD}.parse_segment_header", side_effect=MalformedPacketError("bad hdr")):
            await dev._handle_segment(0x0001, 0x0010, b"\x00" * 8)  # Must not raise


# ---------------------------------------------------------------------------
# _complete_reassembly — edge cases
# ---------------------------------------------------------------------------


class TestCompleteReassembly:
    """Test _complete_reassembly edge cases."""

    @pytest.mark.asyncio
    async def test_buf_already_popped_returns_silently(self) -> None:
        """buf_key not in _segment_buffers → pop returns None → should return."""
        dev = _make_device()
        async with dev._segment_lock:
            await dev._complete_reassembly((0x0001, 0x0010, 100, 0))  # buffer absent

    @pytest.mark.asyncio
    async def test_malformed_opcode_after_reassembly_returns_silently(self) -> None:
        """If parse_access_opcode fails on reassembled payload, should log and return."""
        dev = _make_device()

        # Insert a fake buffer with seg_n=0 (single segment so it's complete)
        from tuya_ble_mesh.sig_mesh_device_segments import _ReassemblyBuffer

        buf = _ReassemblyBuffer(
            src=0x0001,
            dst=0x0010,
            akf=0,
            aid=0,
            szmic=0,
            seq_zero=50,
            seg_n=0,
        )
        buf.segments[0] = b"\x00" * 8  # fake segment data
        buf_key = (0x0001, 0x0010, 50, 0)
        dev._segment_buffers[buf_key] = buf

        with (
            patch(f"{_MOD}.reassemble_and_decrypt_segments", return_value=b"\xff\xff\xff"),
            patch(f"{_MOD}.parse_access_opcode", side_effect=MalformedPacketError("bad op")),
        ):
            async with dev._segment_lock:
                await dev._complete_reassembly(buf_key)


# ---------------------------------------------------------------------------
# _dispatch_access_payload — malformed opcode
# ---------------------------------------------------------------------------


class TestDispatchAccessPayload:
    """Test _dispatch_access_payload guards."""

    @pytest.mark.asyncio
    async def test_malformed_opcode_returns_silently(self) -> None:
        dev = _make_device()
        with patch(f"{_MOD}.parse_access_opcode", side_effect=MalformedPacketError("bad op")):
            await dev._dispatch_access_payload(0x0001, b"\xff")  # Must not raise


# ---------------------------------------------------------------------------
# _dispatch_access_payload_unlocked — pending response matching
# ---------------------------------------------------------------------------


class TestDispatchPayloadUnlocked:
    """Test pending response future resolution."""

    @pytest.mark.asyncio
    async def test_resolves_matching_pending_future(self) -> None:
        """If a pending response matches opcode, future should be resolved."""
        from tuya_ble_mesh.sig_mesh_device_segments import _OPCODE_APPKEY_STATUS

        dev = _make_device()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        resp_key = (_OPCODE_APPKEY_STATUS, 0)
        dev._pending_responses[resp_key] = future

        async with dev._segment_lock:
            await dev._dispatch_access_payload_unlocked(0x0001, _OPCODE_APPKEY_STATUS, b"\x00")

        assert future.done()
        assert future.result() == b"\x00"
        assert resp_key not in dev._pending_responses

    @pytest.mark.asyncio
    async def test_future_already_done_is_skipped(self) -> None:
        """If pending future is already done, set_result should not be called again."""
        from tuya_ble_mesh.sig_mesh_device_segments import _OPCODE_MODEL_APP_STATUS

        dev = _make_device()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bytes] = loop.create_future()
        future.set_result(b"\x99")  # already resolved
        resp_key = (_OPCODE_MODEL_APP_STATUS, 0)
        dev._pending_responses[resp_key] = future

        async with dev._segment_lock:
            await dev._dispatch_access_payload_unlocked(0x0001, _OPCODE_MODEL_APP_STATUS, b"\x00")

        # Future result should still be the original value
        assert future.result() == b"\x99"


# ---------------------------------------------------------------------------
# Callback exception handling
# ---------------------------------------------------------------------------


class TestCallbackExceptionHandling:
    """Test that exceptions in callbacks are caught and logged, not propagated."""

    @pytest.mark.asyncio
    async def test_onoff_callback_exception_does_not_propagate(self) -> None:
        dev = _make_device()

        def bad_onoff(state: bool) -> None:
            raise RuntimeError("onoff boom")

        dev.register_onoff_callback(bad_onoff)
        # Should log warning, not raise
        await dev._dispatch_access_payload_unlocked(
            0x0001,
            0x8204,  # _OPCODE_ONOFF_STATUS
            b"\x01",  # ON
        )

    @pytest.mark.asyncio
    async def test_vendor_callback_exception_does_not_propagate(self) -> None:
        dev = _make_device()

        def bad_vendor(opcode: int, params: bytes) -> None:
            raise RuntimeError("vendor boom")

        dev.register_vendor_callback(bad_vendor)
        # Vendor opcode > 0xFFFF
        await dev._dispatch_access_payload_unlocked(
            0x0001,
            0x010000,  # 3-byte vendor opcode
            b"\x01\x02",
        )

    def test_composition_callback_exception_does_not_propagate(self) -> None:
        """Exception in composition callback should be swallowed."""
        import struct

        dev = _make_device()

        def bad_comp(comp: object) -> None:
            raise RuntimeError("comp boom")

        dev.register_composition_callback(bad_comp)

        page = b"\x00"
        cid = struct.pack("<H", 0x07D0)
        pid = struct.pack("<H", 0x0001)
        vid = struct.pack("<H", 0x0002)
        crpl = struct.pack("<H", 10)
        features = struct.pack("<H", 0x0003)
        params = page + cid + pid + vid + crpl + features

        # Should log warning, not raise
        dev._handle_composition_data(params)

    def test_disconnect_callback_exception_does_not_propagate(self) -> None:
        """Exception in disconnect callback should be swallowed."""
        dev = _make_device()

        def bad_disconnect() -> None:
            raise RuntimeError("disconnect boom")

        dev.register_disconnect_callback(bad_disconnect)
        # Should log warning, not raise
        dev._on_ble_disconnect(MagicMock())
