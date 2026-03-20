"""Unit tests for device_dispatcher._CommandDispatcher.

Covers the async worker paths in device_dispatcher.py that were previously
untested:

- _log_worker_exception: task exception logging
- stop(): queue drain when items remain
- _worker(): TTL expiry, device not connected wait, _running=False during wait,
  send raises DisconnectedError/ConnectionError, unexpected exception handling
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.device_dispatcher import (  # noqa: E402
    _CommandDispatcher,
    _QueuedCommand,
)
from tuya_ble_mesh.exceptions import (  # noqa: E402
    ConnectionError as MeshConnectionError,
)
from tuya_ble_mesh.exceptions import (  # noqa: E402
    DisconnectedError,
)


def _make_mock_device(connected: bool = True) -> MagicMock:
    """Create a minimal mock MeshDevice."""
    device = MagicMock()
    device.is_connected = connected
    device._connected_event = asyncio.Event()
    if connected:
        device._connected_event.set()
    device._send_now = AsyncMock()
    return device


# ---------------------------------------------------------------------------
# _log_worker_exception
# ---------------------------------------------------------------------------


class TestLogWorkerException:
    """Test _log_worker_exception handles task outcomes."""

    @pytest.mark.asyncio
    async def test_cancelled_task_returns_silently(self) -> None:
        task: asyncio.Task[None] = asyncio.create_task(asyncio.sleep(10))
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # Must not raise
        _CommandDispatcher._log_worker_exception(task)

    @pytest.mark.asyncio
    async def test_task_with_exception_logs_error(self) -> None:
        async def fail() -> None:
            raise RuntimeError("worker crash")

        task: asyncio.Task[None] = asyncio.create_task(fail())
        with contextlib.suppress(RuntimeError):
            await task
        # Must not raise; should log error
        _CommandDispatcher._log_worker_exception(task)

    @pytest.mark.asyncio
    async def test_successful_task_does_nothing(self) -> None:
        async def ok() -> None:
            return

        task: asyncio.Task[None] = asyncio.create_task(ok())
        await task
        _CommandDispatcher._log_worker_exception(task)


# ---------------------------------------------------------------------------
# stop() — queue drain
# ---------------------------------------------------------------------------


class TestStop:
    """Test stop() drains remaining queue items."""

    @pytest.mark.asyncio
    async def test_stop_drains_queue(self) -> None:
        """Items in queue when stop() is called should be drained."""
        device = _make_mock_device()
        dispatcher = _CommandDispatcher(device)
        dispatcher.start()

        # Enqueue without letting the worker run
        await dispatcher._queue.put(MagicMock())
        await dispatcher._queue.put(MagicMock())
        assert not dispatcher._queue.empty()

        await dispatcher.stop()

        assert dispatcher._queue.empty()
        assert not dispatcher._running

    @pytest.mark.asyncio
    async def test_stop_when_not_running_is_no_op(self) -> None:
        device = _make_mock_device()
        dispatcher = _CommandDispatcher(device)
        # Never started — _running is False
        await dispatcher.stop()  # Must not raise

    @pytest.mark.asyncio
    async def test_stop_twice_is_idempotent(self) -> None:
        device = _make_mock_device()
        dispatcher = _CommandDispatcher(device)
        dispatcher.start()
        await dispatcher.stop()
        await dispatcher.stop()  # Second stop must not raise


# ---------------------------------------------------------------------------
# _worker() — TTL expiry
# ---------------------------------------------------------------------------


class TestWorkerTTL:
    """Test worker drops commands that have exceeded TTL."""

    @pytest.mark.asyncio
    async def test_expired_command_is_dropped(self) -> None:
        """A command older than TTL should be dropped without being sent."""
        device = _make_mock_device(connected=True)
        dispatcher = _CommandDispatcher(device, ttl=60.0)
        dispatcher.start()

        # Directly inject a command with a very old created_at into the queue
        cmd = _QueuedCommand(0x01, b"\x00", 0x0001)
        cmd.created_at = time.monotonic() - 120.0  # 120s old, TTL is 60s
        await dispatcher._queue.put(cmd)

        # Give the worker time to dequeue and drop the expired command
        await asyncio.sleep(0.05)

        await dispatcher.stop()

        # Command was expired — should not have been sent
        device._send_now.assert_not_called()


# ---------------------------------------------------------------------------
# _worker() — device not connected, waits for connection
# ---------------------------------------------------------------------------


class TestWorkerConnectionWait:
    """Test worker waits for device to connect before sending."""

    @pytest.mark.asyncio
    async def test_worker_waits_then_sends_when_connected(self) -> None:
        """Worker should wait for _connected_event then send the command."""
        device = _make_mock_device(connected=False)
        dispatcher = _CommandDispatcher(device)
        dispatcher.start()

        await dispatcher.enqueue(0x01, b"\xff", 0x0001)

        # Simulate device connecting after a short delay
        await asyncio.sleep(0.02)
        device.is_connected = True
        device._connected_event.set()

        # Give worker time to process
        await asyncio.sleep(0.05)

        await dispatcher.stop()
        device._send_now.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_exits_when_stopped_while_waiting(self) -> None:
        """If dispatcher is stopped while waiting for connection, worker should exit."""
        device = _make_mock_device(connected=False)
        # Never set connected — worker will be stuck waiting
        dispatcher = _CommandDispatcher(device)
        dispatcher.start()

        await dispatcher.enqueue(0x01, b"\x00", 0x0001)

        # Stop while worker is waiting for connection
        await asyncio.sleep(0.05)
        await dispatcher.stop()

        device._send_now.assert_not_called()


# ---------------------------------------------------------------------------
# _worker() — send raises DisconnectedError or ConnectionError
# ---------------------------------------------------------------------------


class TestWorkerSendFailure:
    """Test worker handles send failures gracefully."""

    @pytest.mark.asyncio
    async def test_disconnected_error_is_logged_and_dropped(self) -> None:
        """DisconnectedError during send should be caught and command dropped."""
        device = _make_mock_device(connected=True)
        device._send_now = AsyncMock(side_effect=DisconnectedError("disconnected"))
        dispatcher = _CommandDispatcher(device)
        dispatcher.start()

        await dispatcher.enqueue(0x01, b"\x00", 0x0001)
        await asyncio.sleep(0.05)
        await dispatcher.stop()

        # send_now was called (and raised, but error was swallowed)
        device._send_now.assert_called_once()

    @pytest.mark.asyncio
    async def test_connection_error_is_logged_and_dropped(self) -> None:
        """ConnectionError during send should be caught and command dropped."""
        device = _make_mock_device(connected=True)
        device._send_now = AsyncMock(side_effect=MeshConnectionError("no connection"))
        dispatcher = _CommandDispatcher(device)
        dispatcher.start()

        await dispatcher.enqueue(0x01, b"\x00", 0x0001)
        await asyncio.sleep(0.05)
        await dispatcher.stop()

        device._send_now.assert_called_once()


# ---------------------------------------------------------------------------
# _worker() — unexpected exception (outer try)
# ---------------------------------------------------------------------------


class TestWorkerUnexpectedException:
    """Test worker catches and logs unexpected exceptions."""

    @pytest.mark.asyncio
    async def test_unexpected_exception_does_not_crash_worker(self) -> None:
        """Generic exception in worker loop should be caught, logged, and loop continues.

        The worker catches the OSError, logs it, then enters asyncio.sleep(1).
        We stop the dispatcher while it's sleeping, which cancels the task gracefully.
        """
        device = _make_mock_device(connected=True)
        device._send_now = AsyncMock(side_effect=OSError("unexpected IO error"))
        dispatcher = _CommandDispatcher(device)
        dispatcher.start()

        await dispatcher.enqueue(0x01, b"\x00", 0x0001)

        # Give the worker enough time to process the command and enter the
        # except Exception handler (asyncio.sleep(1) inside the handler)
        await asyncio.sleep(0.05)

        # Worker is now blocked in asyncio.sleep(1) — stop() cancels it
        await dispatcher.stop()

        # _send_now was called (and raised OSError, which was caught)
        device._send_now.assert_called_once()
