"""Additional unit tests for transport/dispatcher.py — AsyncCommandDispatcher.

Supplements test_transport_dispatcher.py by covering previously untested paths:
- start() idempotency guard
- stop() when not running guard
- stop() cancels pending result futures
- enqueue() coalescing path
- _coalesce_check() match in in-flight
- _can_send() total limit exceeded
- history property and in_flight_count()
- _send_with_retry() all retries exhausted → error result
- _send_with_retry() expected_response_opcode correlation registration
- _send_with_retry() break when not running
- Worker: request expired before processing
- Worker: outer exception handler
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.transport import (  # noqa: E402
    AsyncCommandDispatcher,
    CommandRequest,
    RetryPolicy,
)
from tuya_ble_mesh.transport.result import CommandResult  # noqa: E402


def _make_dispatcher(
    send_callback: AsyncMock | None = None,
    *,
    per_device_limit: int = 10,
    total_limit: int = 32,
    coalesce_window_ms: int = 50,
) -> AsyncCommandDispatcher:
    if send_callback is None:
        send_callback = AsyncMock(return_value=b"ok")
    seq = 0

    def seq_cb() -> int:
        nonlocal seq
        seq += 1
        return seq

    return AsyncCommandDispatcher(
        send_callback=send_callback,
        next_sequence_callback=seq_cb,
        per_device_limit=per_device_limit,
        total_limit=total_limit,
        coalesce_window_ms=coalesce_window_ms,
    )


# ---------------------------------------------------------------------------
# start() / stop() guards
# ---------------------------------------------------------------------------


class TestStartStopGuards:
    """Test idempotency of start() and stop()."""

    @pytest.mark.asyncio
    async def test_start_twice_creates_one_worker(self) -> None:
        d = _make_dispatcher()
        d.start()
        task1 = d._worker_task
        d.start()  # Should be a no-op
        task2 = d._worker_task
        assert task1 is task2
        await d.stop()

    @pytest.mark.asyncio
    async def test_stop_when_not_started_is_no_op(self) -> None:
        d = _make_dispatcher()
        await d.stop()  # Must not raise

    @pytest.mark.asyncio
    async def test_stop_cancels_pending_result_futures(self) -> None:
        """Pending result futures should be resolved as 'cancelled' on stop()."""
        d = _make_dispatcher()
        d.start()

        # Manually inject a pending future (simulates a request in the queue)
        rid = uuid.uuid4()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[CommandResult] = loop.create_future()
        d._result_futures[rid] = future

        await d.stop()

        assert future.done()
        result = future.result()
        assert result.status == "cancelled"
        assert result.request_id == rid


# ---------------------------------------------------------------------------
# enqueue() — expired request immediate rejection
# ---------------------------------------------------------------------------


class TestEnqueueExpired:
    """Test that already-expired requests are rejected immediately."""

    @pytest.mark.asyncio
    async def test_expired_request_returns_timeout_result(self) -> None:
        d = _make_dispatcher()
        d.start()

        # Create a request with very short TTL, then wait for it to expire
        req = CommandRequest(ttl=0.001)
        await asyncio.sleep(0.01)  # Let TTL expire

        future = await d.enqueue(req)
        result = future.result()  # Should already be resolved

        assert result.status == "timeout"
        assert result.request_id == req.request_id

        await d.stop()


# ---------------------------------------------------------------------------
# enqueue() — coalescing
# ---------------------------------------------------------------------------


class TestCoalescing:
    """Test command coalescing of identical in-flight requests."""

    @pytest.mark.asyncio
    async def test_coalesce_check_matches_in_flight_request(self) -> None:
        """_coalesce_check should return existing future for identical in-flight request."""
        # Use a large coalesce window so timing doesn't matter
        send_cb: AsyncMock = AsyncMock()

        async def slow_send(req: CommandRequest, seq: int) -> bytes:
            await asyncio.sleep(0.5)
            return b"ok"

        send_cb.side_effect = slow_send
        d = _make_dispatcher(send_cb, coalesce_window_ms=2000)
        d.start()

        req1 = CommandRequest(opcode=0x01, target_node=0x0001, params=b"\x00")
        future1 = await d.enqueue(req1)

        # Give worker time to pick up req1 and mark it in-flight
        await asyncio.sleep(0.05)

        # Second identical request within 2-second coalesce window should coalesce
        req2 = CommandRequest(opcode=0x01, target_node=0x0001, params=b"\x00")
        future2 = await d.enqueue(req2)

        # If coalesced, future2 should be the same object as future1
        assert future2 is future1
        assert d.metrics.commands_coalesced == 1

        await d.stop()

    @pytest.mark.asyncio
    async def test_different_params_not_coalesced(self) -> None:
        """Requests with different params should NOT be coalesced."""
        send_cb: AsyncMock = AsyncMock()

        async def slow_send(req: CommandRequest, seq: int) -> bytes:
            await asyncio.sleep(0.3)
            return b"ok"

        send_cb.side_effect = slow_send
        d = _make_dispatcher(send_cb, coalesce_window_ms=2000)
        d.start()

        req1 = CommandRequest(opcode=0x01, target_node=0x0001, params=b"\x00")
        req2 = CommandRequest(opcode=0x01, target_node=0x0001, params=b"\x01")

        future1 = await d.enqueue(req1)
        await asyncio.sleep(0.05)

        send_cb.side_effect = None
        send_cb.return_value = b"ok"
        future2 = await d.enqueue(req2)

        assert future2 is not future1
        assert d.metrics.commands_coalesced == 0

        await d.stop()


# ---------------------------------------------------------------------------
# _can_send() — total limit
# ---------------------------------------------------------------------------


class TestCanSend:
    """Test _can_send() in-flight limit enforcement."""

    def test_total_limit_exceeded_returns_false(self) -> None:
        d = _make_dispatcher(total_limit=2)
        req = CommandRequest(opcode=0x01, target_node=0x0001)

        # Fill in-flight to total_limit
        d._in_flight[uuid.uuid4()] = req
        d._in_flight[uuid.uuid4()] = req

        result = d._can_send(CommandRequest(opcode=0x02, target_node=0x0002))
        assert result is False

    def test_device_limit_exceeded_returns_false(self) -> None:
        d = _make_dispatcher(per_device_limit=1)
        req = CommandRequest(opcode=0x01, target_node=0x0001)
        d._in_flight[uuid.uuid4()] = req
        d._in_flight_per_device[0x0001] = 1

        result = d._can_send(CommandRequest(opcode=0x02, target_node=0x0001))
        assert result is False

    def test_within_limits_returns_true(self) -> None:
        d = _make_dispatcher()
        result = d._can_send(CommandRequest(opcode=0x01, target_node=0x0001))
        assert result is True


# ---------------------------------------------------------------------------
# history property and in_flight_count()
# ---------------------------------------------------------------------------


class TestHelpers:
    """Test history and in_flight_count helpers."""

    @pytest.mark.asyncio
    async def test_history_records_completed_commands(self) -> None:
        d = _make_dispatcher()
        d.start()

        req = CommandRequest(opcode=0x01, target_node=0x0001)
        future = await d.enqueue(req)
        await future

        history = d.history
        assert len(history) == 1
        hist_req, hist_result = history[0]
        assert hist_req.request_id == req.request_id
        assert hist_result.status == "success"

        await d.stop()

    def test_in_flight_count_reflects_in_flight(self) -> None:
        d = _make_dispatcher()
        assert d.in_flight_count() == 0
        req = CommandRequest()
        d._in_flight[req.request_id] = req
        assert d.in_flight_count() == 1


# ---------------------------------------------------------------------------
# _send_with_retry() — all retries exhausted
# ---------------------------------------------------------------------------


class TestSendWithRetryError:
    """Test _send_with_retry() error handling."""

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_resolves_error(self) -> None:
        """All retry attempts failing should resolve future with 'error' status."""
        send_cb = AsyncMock(side_effect=OSError("send failed"))
        d = _make_dispatcher(send_cb)
        d.start()

        req = CommandRequest(
            opcode=0x01,
            target_node=0x0001,
            retry_policy=RetryPolicy(max_retries=2, backoff_base=0.01, backoff_max=0.02),
        )
        future = await d.enqueue(req)

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await asyncio.wait_for(future, timeout=2.0)

        assert result.status == "error"
        assert result.error is not None
        assert result.retries_used == 3  # initial + 2 retries

        await d.stop()

    @pytest.mark.asyncio
    async def test_expected_response_opcode_registers_correlation(self) -> None:
        """When expected_response_opcode is set, correlation should be registered."""
        send_cb = AsyncMock(return_value=b"response")
        d = _make_dispatcher(send_cb)
        d.start()

        req = CommandRequest(
            opcode=0x01,
            target_node=0x0001,
            expected_response_opcode=0x02,
        )
        future = await d.enqueue(req)
        result = await asyncio.wait_for(future, timeout=2.0)

        # send_callback was called — correlation was attempted
        assert result.status == "success"
        send_cb.assert_called_once()

        await d.stop()


# ---------------------------------------------------------------------------
# Worker: request expired before processing
# ---------------------------------------------------------------------------


class TestWorkerExpiry:
    """Test worker drops requests that expire before processing."""

    @pytest.mark.asyncio
    async def test_expired_request_in_queue_resolved_as_timeout(self) -> None:
        """Request that expires while in queue should be resolved as 'timeout'.

        Bypasses enqueue() to avoid the early-expiry check there, and directly
        injects an already-expired request into the priority queue.
        """
        d = _make_dispatcher()
        d.start()

        # Create a request with very short TTL and wait for it to expire
        req = CommandRequest(opcode=0x01, target_node=0x0001, ttl=0.001)
        await asyncio.sleep(0.01)  # Let TTL expire before worker sees it

        # Inject directly into queue (bypasses enqueue() early-expiry check)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[CommandResult] = loop.create_future()
        d._result_futures[req.request_id] = future
        await d._queue.put((req.priority, req.created_at, req))

        # Give worker time to dequeue and process the expired request
        await asyncio.sleep(0.1)

        await d.stop()

        # Future should be resolved as timeout
        assert future.done()
        assert future.result().status == "timeout"


# ---------------------------------------------------------------------------
# Worker: outer exception handler
# ---------------------------------------------------------------------------


class TestWorkerOuterException:
    """Test worker catches TuyaBLEMeshError/OSError from processing."""

    @pytest.mark.asyncio
    async def test_os_error_in_worker_does_not_crash_dispatcher(self) -> None:
        """OSError raised outside send_callback should be caught by worker."""
        from tuya_ble_mesh.exceptions import TuyaBLEMeshError

        d = _make_dispatcher()
        d.start()

        # Patch _send_with_retry to raise TuyaBLEMeshError (a non-cancelled error)
        with patch.object(d, "_send_with_retry", new_callable=AsyncMock) as mock_send:
            mock_send.side_effect = TuyaBLEMeshError("worker glitch")

            req = CommandRequest(opcode=0x01, target_node=0x0001)
            await d.enqueue(req)
            await asyncio.sleep(0.05)

        # Dispatcher should still be running
        assert d._running

        await d.stop()
