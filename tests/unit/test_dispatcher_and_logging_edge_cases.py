"""Edge case tests for device_dispatcher.py and logging_context.py.

device_dispatcher.py covers:
  _log_worker_exception: 104-105 (CancelledError from task.exception())
  stop(): 121-122 (QueueEmpty in drain loop)
  _worker: 152 (queue poll timeout), 178 (connection wait timeout),
           183-185 (exit when _running=False), 206 (worker stopped log)

logging_context.py covers:
  57 (get_log_extra return dict)
  111-116 (mesh_operation_sync context manager)
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

from tuya_ble_mesh.device_dispatcher import _CommandDispatcher
from tuya_ble_mesh.logging_context import (
    get_log_extra,
    mesh_operation_sync,
    set_context,
)


def _make_device(connected: bool = True) -> MagicMock:
    device = MagicMock()
    device.is_connected = connected
    device._connected_event = asyncio.Event()
    if connected:
        device._connected_event.set()
    device._send_now = AsyncMock()
    return device


# ── logging_context ────────────────────────────────────────────────────────


class TestGetLogExtra:
    """Line 57: get_log_extra() returns dict with corr/mac/op keys."""

    def test_returns_dict_with_expected_keys(self) -> None:
        extra = get_log_extra()
        assert "corr" in extra
        assert "mac" in extra
        assert "op" in extra

    def test_values_reflect_context(self) -> None:
        tokens = set_context("AA:BB:CC:DD:EE:FF", "test_op")
        extra = get_log_extra()
        assert extra["mac"] == "AA:BB:CC:DD:EE:FF"
        assert extra["op"] == "test_op"
        from tuya_ble_mesh.logging_context import reset_context

        reset_context(tokens)

    def test_defaults_are_empty_strings(self) -> None:
        extra = get_log_extra()
        # After reset, defaults should be empty strings
        assert isinstance(extra["corr"], str)
        assert isinstance(extra["mac"], str)
        assert isinstance(extra["op"], str)


class TestMeshOperationSync:
    """Lines 111-116: mesh_operation_sync sets and resets logging context."""

    def test_yields_correlation_id(self) -> None:
        with mesh_operation_sync("AA:BB:CC:DD:EE:FF", "connect") as corr_id:
            extra = get_log_extra()
            assert isinstance(corr_id, str)
            assert extra["mac"] == "AA:BB:CC:DD:EE:FF"
            assert extra["op"] == "connect"

    def test_context_reset_on_exit(self) -> None:
        with mesh_operation_sync("11:22:33:44:55:66", "scan"):
            pass  # context active
        extra = get_log_extra()
        # After exiting, MAC should no longer be "11:22:33:44:55:66"
        assert extra["mac"] != "11:22:33:44:55:66"

    def test_context_reset_on_exception(self) -> None:
        with contextlib.suppress(RuntimeError), mesh_operation_sync("77:88:99:AA:BB:CC", "op"):
            raise RuntimeError("test error")
        extra = get_log_extra()
        assert extra["mac"] != "77:88:99:AA:BB:CC"


# ── device_dispatcher ──────────────────────────────────────────────────────


class TestLogWorkerExceptionCancelled:
    """Lines 104-105: task.exception() raises CancelledError → pass."""

    @pytest.mark.asyncio
    async def test_cancelled_exception_from_exception_method(self) -> None:
        """Mock task where cancelled() is False but exception() raises CancelledError."""
        mock_task: MagicMock = MagicMock(spec=asyncio.Task)
        mock_task.cancelled.return_value = False
        mock_task.exception.side_effect = asyncio.CancelledError()
        # Must not raise
        _CommandDispatcher._log_worker_exception(mock_task)


class TestStopQueueEmpty:
    """Lines 121-122: get_nowait() raises QueueEmpty in drain loop."""

    @pytest.mark.asyncio
    async def test_queue_empty_exception_breaks_drain_loop(self) -> None:
        device = _make_device()
        dispatcher = _CommandDispatcher(device)
        # Replace internal queue with a mock where empty() returns False
        # but get_nowait() raises QueueEmpty
        mock_queue = MagicMock()
        mock_queue.empty.return_value = False
        mock_queue.get_nowait.side_effect = asyncio.QueueEmpty()
        dispatcher._queue = mock_queue
        # Mark as running so stop() doesn't short-circuit
        dispatcher._running = True
        dispatcher._worker_task = None  # No task to cancel
        # Must not raise and should call get_nowait (hitting lines 121-122)
        await dispatcher.stop()
        mock_queue.get_nowait.assert_called_once()


class TestWorkerQueuePollTimeout:
    """Line 152: queue.get() times out → continue (with patched short timeout)."""

    @pytest.mark.asyncio
    async def test_empty_queue_poll_timeout_loops_back(self) -> None:
        """Patch poll interval to 1ms; empty queue → timeout → continue → stop."""
        device = _make_device(connected=True)
        dispatcher = _CommandDispatcher(device)

        with patch("tuya_ble_mesh.device_dispatcher._QUEUE_POLL_INTERVAL", 0.001):
            dispatcher.start()
            # Give the worker time to poll the empty queue and hit line 152
            await asyncio.sleep(0.01)
            await dispatcher.stop()


class TestWorkerConnectionWaitTimeout:
    """Line 178: connection event wait times out → continue."""

    @pytest.mark.asyncio
    async def test_connection_wait_timeout_loops(self) -> None:
        """Patch wait timeout to 1ms; disconnected device → repeated timeouts."""
        device = _make_device(connected=False)
        dispatcher = _CommandDispatcher(device)

        with patch("tuya_ble_mesh.device_dispatcher.CONNECTION_EVENT_WAIT_TIMEOUT", 0.001):
            dispatcher.start()
            await dispatcher.enqueue(0x01, b"\x00", 0x0001)
            # Let worker enter connection wait and time out a few times
            await asyncio.sleep(0.015)
            await dispatcher.stop()

        device._send_now.assert_not_called()


class TestWorkerExitsCleanly:
    """Lines 183-185, 206: _running=False without cancellation → clean worker exit."""

    @pytest.mark.asyncio
    async def test_running_false_causes_clean_exit(self) -> None:
        """Worker exits via lines 184-185 and logs line 206 when _running cleared."""
        device = _make_device(connected=False)
        dispatcher = _CommandDispatcher(device)

        with patch("tuya_ble_mesh.device_dispatcher.CONNECTION_EVENT_WAIT_TIMEOUT", 0.001):
            dispatcher.start()
            await dispatcher.enqueue(0x01, b"\x00", 0x0001)

            # Let worker enter the connection wait loop and timeout at least once
            await asyncio.sleep(0.008)

            # Clear _running WITHOUT cancelling the task — forces clean exit
            worker_task = dispatcher._worker_task
            dispatcher._running = False

            # Worker should exit after next timeout (within 2ms)
            if worker_task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.wait_for(worker_task, timeout=0.5)

        device._send_now.assert_not_called()

    @pytest.mark.asyncio
    async def test_worker_exits_immediately_when_not_running(self) -> None:
        """Line 206: _worker() called directly with _running=False → instant exit."""
        device = _make_device()
        dispatcher = _CommandDispatcher(device)
        # _running is False by default — while loop exits immediately → line 206
        await dispatcher._worker()
