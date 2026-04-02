"""Unit tests for transport/dispatcher.py — uncovered edge cases.

Covers:
  _worker: 232 (_complete_request for expired stale correlation entries)
  _worker: 247 (queue wait_for TimeoutError → continue)
  _worker: 274 (not self._running → break after capacity wait)
  _worker: 306-310 (TuyaBLEMeshError/OSError re-raised by worker)
  _send_with_retry: 342 (not self._running → break in retry loop)
  queue_depth: 461 (return self._queue.qsize())
"""

from __future__ import annotations

import asyncio
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

from tuya_ble_mesh.exceptions import TuyaBLEMeshError
from tuya_ble_mesh.transport.dispatcher import AsyncCommandDispatcher
from tuya_ble_mesh.transport.request import CommandRequest, RetryPolicy
from tuya_ble_mesh.transport.result import CommandResult


def _make_dispatcher(
    *,
    send_side_effect: object = None,
    send_return: bytes = b"",
    per_device_limit: int = 3,
) -> tuple[AsyncCommandDispatcher, AsyncMock]:
    """Create a dispatcher with a mocked send callback."""
    if send_side_effect is not None:
        send_cb: AsyncMock = AsyncMock(side_effect=send_side_effect)
    else:
        send_cb = AsyncMock(return_value=send_return)
    seq_cb: MagicMock = MagicMock(return_value=1)
    dispatcher = AsyncCommandDispatcher(
        send_cb,
        seq_cb,
        per_device_limit=per_device_limit,
    )
    return dispatcher, send_cb


# ── line 232: expired stale correlation entry ────────────────────────────────


class TestExpiredStaleCorrelationEntry:
    """Line 232: worker completes stale correlation entries with 'timeout'."""

    @pytest.mark.asyncio
    async def test_expired_entry_completes_with_timeout(self) -> None:
        """Line 232: expire_stale returns an entry → _complete_request called."""
        dispatcher, _ = _make_dispatcher()

        request = CommandRequest(
            target_node=0x0001,
            opcode=0x0100,
            ttl=0.001,  # expires in 1 ms
        )
        future: asyncio.Future[CommandResult] = asyncio.Future()
        dispatcher._result_futures[request.request_id] = future

        # Register in correlation engine so expire_stale can find it
        dispatcher._correlation.register(request, sequence=42)

        # Wait for the request deadline to pass
        await asyncio.sleep(0.01)

        with patch("tuya_ble_mesh.transport.dispatcher.QUEUE_POLL_INTERVAL", 0.001):
            dispatcher.start()
            await asyncio.sleep(0.05)
            await dispatcher.stop()

        assert future.done()
        assert future.result().status == "timeout"


# ── line 247: queue timeout → continue ──────────────────────────────────────


class TestQueueTimeoutContinue:
    """Line 247: asyncio.wait_for times out → continue (worker keeps running)."""

    @pytest.mark.asyncio
    async def test_worker_stays_alive_after_queue_timeout(self) -> None:
        """Line 247: empty queue → TimeoutError → continue, worker not dead."""
        dispatcher, _ = _make_dispatcher()

        with patch("tuya_ble_mesh.transport.dispatcher.QUEUE_POLL_INTERVAL", 0.001):
            dispatcher.start()
            # Allow enough time for several queue-wait timeouts to fire
            await asyncio.sleep(0.05)

            assert dispatcher._running
            assert dispatcher._worker_task is not None
            assert not dispatcher._worker_task.done()

            await dispatcher.stop()


# ── line 274: not self._running → break after capacity wait ─────────────────


class TestWorkerBreaksWhenNotRunningDuringCapacityWait:
    """Lines 274, 310: _running=False while waiting for capacity → break."""

    @pytest.mark.asyncio
    async def test_worker_breaks_when_running_set_false(self) -> None:
        """Lines 274, 310: worker exits cleanly when _running cleared mid-wait."""
        dispatcher, _ = _make_dispatcher(per_device_limit=1)

        # Fill the in-flight slot for device 0x0001 so _can_send returns False
        dummy = CommandRequest(target_node=0x0001, opcode=0x01)
        dispatcher._in_flight[dummy.request_id] = dummy
        dispatcher._in_flight_per_device[0x0001] = 1

        with patch("tuya_ble_mesh.transport.dispatcher.QUEUE_POLL_INTERVAL", 0.001):
            dispatcher.start()

            # Enqueue a request blocked behind the in-flight limit
            blocked = CommandRequest(target_node=0x0001, opcode=0x01)
            await dispatcher.enqueue(blocked)

            # Let the worker dequeue the item and enter the capacity-wait loop
            await asyncio.sleep(0.005)

            # Clear _running so the worker detects it on the next sleep wake-up
            dispatcher._running = False
            await asyncio.sleep(0.02)

        # Worker should have exited via break at line 274 (normal return, no exc)
        assert dispatcher._worker_task is not None
        assert dispatcher._worker_task.done()
        assert dispatcher._worker_task.result() is None


# ── lines 306-310: unexpected error in worker ───────────────────────────────


class TestWorkerUnexpectedErrorReraises:
    """Lines 306-308: TuyaBLEMeshError inside the worker loop → re-raised."""

    @pytest.mark.asyncio
    async def test_tuya_error_in_expire_stale_reraises(self) -> None:
        """Lines 306-308: TuyaBLEMeshError from expire_stale kills worker task."""
        dispatcher, _ = _make_dispatcher()

        with patch.object(
            dispatcher._correlation,
            "expire_stale",
            side_effect=TuyaBLEMeshError("unexpected"),
        ):
            dispatcher.start()
            # Yield to let the worker execute its first iteration
            await asyncio.sleep(0.02)

        assert dispatcher._worker_task is not None
        assert dispatcher._worker_task.done()
        with pytest.raises(TuyaBLEMeshError, match="unexpected"):
            dispatcher._worker_task.result()


# ── line 342: not self._running → break in _send_with_retry ─────────────────


class TestSendWithRetryBreaksWhenNotRunning:
    """Line 342: _running cleared between retries → break in retry for-loop."""

    @pytest.mark.asyncio
    async def test_running_false_stops_retry_loop(self) -> None:
        """Line 342: _running=False before second attempt → break immediately."""
        call_count = 0
        dispatcher: AsyncCommandDispatcher | None = None

        async def flaky_send(request: CommandRequest, seq: int) -> bytes:
            nonlocal call_count
            call_count += 1
            # After the first send failure, disable the dispatcher
            assert dispatcher is not None
            dispatcher._running = False
            raise OSError("send failed")

        dispatcher = AsyncCommandDispatcher(
            flaky_send,
            MagicMock(return_value=1),
            per_device_limit=3,
        )
        dispatcher._running = True

        request = CommandRequest(
            target_node=0x0001,
            opcode=0x01,
            retry_policy=RetryPolicy(
                max_retries=5,
                backoff_base=0.001,
                backoff_max=0.01,
                jitter=0.0,
            ),
        )
        future: asyncio.Future[CommandResult] = asyncio.Future()
        dispatcher._result_futures[request.request_id] = future
        dispatcher._in_flight[request.request_id] = request
        dispatcher._in_flight_per_device[0x0001] = 1

        await dispatcher._send_with_retry(request)

        # Only one attempt should have been made (broke before attempt 2)
        assert call_count == 1
        assert future.done()
        assert future.result().status == "error"


# ── line 461: queue_depth() ──────────────────────────────────────────────────


class TestQueueDepth:
    """Line 461: queue_depth() returns current queue size."""

    @pytest.mark.asyncio
    async def test_empty_queue_returns_zero(self) -> None:
        """Line 461: freshly created dispatcher has queue depth 0."""
        dispatcher, _ = _make_dispatcher()
        assert dispatcher.queue_depth() == 0

    @pytest.mark.asyncio
    async def test_queue_depth_reflects_enqueued_items(self) -> None:
        """Line 461: depth increases with enqueued requests."""
        dispatcher, _ = _make_dispatcher()

        r1 = CommandRequest(target_node=0x0001, opcode=0x01)
        r2 = CommandRequest(target_node=0x0002, opcode=0x02)
        dispatcher._result_futures[r1.request_id] = asyncio.Future()
        dispatcher._result_futures[r2.request_id] = asyncio.Future()
        await dispatcher._queue.put((1, r1.created_at, r1))
        await dispatcher._queue.put((1, r2.created_at, r2))

        assert dispatcher.queue_depth() == 2
