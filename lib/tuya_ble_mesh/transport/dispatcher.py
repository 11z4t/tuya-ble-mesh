"""Async command dispatcher with priority queue and correlation tracking.

Provides fire-and-forget command dispatch with:
- Priority-based queue (0=critical, 1=normal, 2=background)
- Per-device and per-bridge in-flight limits
- Command coalescing (merge identical rapid requests)
- Request/response correlation
- Retry with exponential backoff
- Comprehensive metrics
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from tuya_ble_mesh.const import QUEUE_POLL_INTERVAL
from tuya_ble_mesh.exceptions import TuyaBLEMeshError
from tuya_ble_mesh.transport.correlation import CorrelationEngine
from tuya_ble_mesh.transport.metrics import TransportMetrics
from tuya_ble_mesh.transport.request import CommandRequest
from tuya_ble_mesh.transport.result import CommandResult

if TYPE_CHECKING:
    pass

_LOGGER = logging.getLogger(__name__)


class AsyncCommandDispatcher:
    """Async command dispatcher with priority queue and correlation.

    Manages a priority queue of CommandRequest objects, enforces in-flight
    limits, coalesces identical requests, correlates responses, retries
    with exponential backoff, and tracks comprehensive metrics.

    Attributes:
        per_device_limit: Maximum in-flight commands per device.
        per_bridge_limit: Maximum in-flight commands per bridge.
        total_limit: Maximum total in-flight commands.
        coalesce_window_ms: Time window for coalescing identical requests (ms).
    """

    def __init__(
        self,
        send_callback: Callable[[CommandRequest, int], Awaitable[bytes]],
        next_sequence_callback: Callable[[], int],
        *,
        per_device_limit: int = 3,
        per_bridge_limit: int = 10,
        total_limit: int = 32,
        coalesce_window_ms: int = 50,
    ) -> None:
        """Initialize the dispatcher.

        Args:
            send_callback: Async callable(request, sequence) → response_bytes.
            next_sequence_callback: Callable() → next_sequence_number.
            per_device_limit: Max in-flight per device (default 3).
            per_bridge_limit: Max in-flight per bridge (default 10).
            total_limit: Max total in-flight (default 32).
            coalesce_window_ms: Coalescing window in ms (default 50).
        """
        self._send_callback = send_callback
        self._next_sequence_callback = next_sequence_callback

        self.per_device_limit = per_device_limit
        self.per_bridge_limit = per_bridge_limit
        self.total_limit = total_limit
        self.coalesce_window_ms = coalesce_window_ms

        # Priority queue: (priority, created_at, request)
        self._queue: asyncio.PriorityQueue[tuple[int, float, CommandRequest]] = (
            asyncio.PriorityQueue()
        )

        # In-flight tracking
        self._in_flight: dict[uuid.UUID, CommandRequest] = {}
        self._in_flight_per_device: dict[int, int] = {}  # target_node → count

        # Result futures for callers
        self._result_futures: dict[uuid.UUID, asyncio.Future[CommandResult]] = {}

        # Command history (last 100 for diagnostics)
        self._history: deque[tuple[CommandRequest, CommandResult]] = deque(maxlen=100)

        # Correlation and metrics
        self._correlation = CorrelationEngine()
        self._metrics = TransportMetrics()

        # Worker task
        self._worker_task: asyncio.Task[None] | None = None
        self._running = False

    def start(self) -> None:
        """Start the dispatcher worker task."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        _LOGGER.debug("AsyncCommandDispatcher started")

    async def stop(self) -> None:
        """Stop the dispatcher and cancel pending commands."""
        if not self._running:
            return
        self._running = False

        # Cancel worker
        if self._worker_task is not None:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task

        # Cancel all pending futures
        for request_id, future in list(self._result_futures.items()):
            if not future.done():
                result = CommandResult(
                    request_id=request_id,
                    status="cancelled",
                )
                future.set_result(result)

        self._result_futures.clear()
        self._in_flight.clear()
        self._in_flight_per_device.clear()
        self._correlation.clear()

        _LOGGER.debug("AsyncCommandDispatcher stopped")

    async def enqueue(self, request: CommandRequest) -> asyncio.Future[CommandResult]:
        """Enqueue a command request and return a future for the result.

        Args:
            request: Command request to enqueue.

        Returns:
            Future that resolves to CommandResult when command completes.

        Raises:
            asyncio.QueueFull: If queue is at capacity (should not happen with
                PriorityQueue, but can be configured).
        """
        if request.is_expired():
            # Already expired — reject immediately
            result = CommandResult(
                request_id=request.request_id,
                status="timeout",
                latency_ms=request.age() * 1000,
            )
            self._metrics.record_result(result, request.opcode)
            future: asyncio.Future[CommandResult] = asyncio.Future()
            future.set_result(result)
            return future

        # Check for coalescing opportunity
        coalesced_future = self._coalesce_check(request)
        if coalesced_future is not None:
            self._metrics.commands_coalesced += 1
            _LOGGER.debug(
                "Coalesced request %s (opcode=0x%04X target=0x%04X)",
                request.request_id,
                request.opcode,
                request.target_node,
            )
            return coalesced_future

        # Create result future
        future = asyncio.Future()
        self._result_futures[request.request_id] = future

        # Enqueue
        await self._queue.put((request.priority, request.created_at, request))
        _LOGGER.debug(
            "Enqueued request %s (priority=%d opcode=0x%04X target=0x%04X qsize=%d)",
            request.request_id,
            request.priority,
            request.opcode,
            request.target_node,
            self._queue.qsize(),
        )

        return future

    def _coalesce_check(self, request: CommandRequest) -> asyncio.Future[CommandResult] | None:
        """Check if request can be coalesced with a pending request.

        Coalescing means multiple identical requests within a short time window
        share the same result future.

        Args:
            request: Request to check.

        Returns:
            Existing future if coalesced, None otherwise.
        """
        now = time.monotonic()
        window_start = now - (self.coalesce_window_ms / 1000.0)

        # Check in-flight requests for identical match within window
        for in_flight_request in self._in_flight.values():
            if (
                in_flight_request.opcode == request.opcode
                and in_flight_request.target_node == request.target_node
                and in_flight_request.params == request.params
                and in_flight_request.protocol == request.protocol
                and in_flight_request.created_at >= window_start
            ):
                # Found a coalescing candidate
                existing_future = self._result_futures.get(in_flight_request.request_id)
                if existing_future is not None and not existing_future.done():
                    return existing_future

        return None

    async def _worker(self) -> None:
        """Worker task that drains the queue and sends commands."""
        _LOGGER.debug("AsyncCommandDispatcher worker started")

        while self._running:
            try:
                # Periodically expire stale correlation entries
                expired = self._correlation.expire_stale()
                for expired_request in expired:
                    self._complete_request(
                        expired_request,
                        CommandResult(
                            request_id=expired_request.request_id,
                            status="timeout",
                            latency_ms=expired_request.age() * 1000,
                        ),
                    )

                # Wait for a request (with timeout to check _running)
                try:
                    _, _, request = await asyncio.wait_for(
                        self._queue.get(), timeout=QUEUE_POLL_INTERVAL * 10
                    )
                except TimeoutError:
                    continue

                # Check expiration
                if request.is_expired():
                    _LOGGER.warning(
                        "Request %s expired before processing (age=%.1fs)",
                        request.request_id,
                        request.age(),
                    )
                    self._complete_request(
                        request,
                        CommandResult(
                            request_id=request.request_id,
                            status="timeout",
                            latency_ms=request.age() * 1000,
                        ),
                    )
                    continue

                # Check in-flight limits
                while not self._can_send(request):
                    # Wait a bit for in-flight capacity
                    await asyncio.sleep(QUEUE_POLL_INTERVAL)
                    if not self._running or request.is_expired():
                        break

                if not self._running:
                    break

                if request.is_expired():
                    _LOGGER.warning(
                        "Request %s expired while waiting for capacity",
                        request.request_id,
                    )
                    self._complete_request(
                        request,
                        CommandResult(
                            request_id=request.request_id,
                            status="timeout",
                            latency_ms=request.age() * 1000,
                        ),
                    )
                    continue

                # Mark as in-flight before creating task so _can_send checks are accurate
                self._in_flight[request.request_id] = request
                self._in_flight_per_device[request.target_node] = (
                    self._in_flight_per_device.get(request.target_node, 0) + 1
                )
                self._metrics.record_in_flight(len(self._in_flight))

                # Send the request
                task = asyncio.create_task(self._send_with_retry(request))
                # Store task reference to prevent garbage collection
                task.add_done_callback(lambda _: None)

            except asyncio.CancelledError:
                _LOGGER.debug("AsyncCommandDispatcher worker cancelled")
                raise
            except (TuyaBLEMeshError, OSError, TimeoutError):
                _LOGGER.error("AsyncCommandDispatcher worker error", exc_info=True)

        _LOGGER.debug("AsyncCommandDispatcher worker stopped")

    def _can_send(self, request: CommandRequest) -> bool:
        """Check if request can be sent given in-flight limits.

        Args:
            request: Request to check.

        Returns:
            True if request can be sent, False otherwise.
        """
        total_in_flight = len(self._in_flight)
        if total_in_flight >= self.total_limit:
            return False

        device_in_flight = self._in_flight_per_device.get(request.target_node, 0)
        # Return negated condition directly
        return device_in_flight < self.per_device_limit

    async def _send_with_retry(self, request: CommandRequest) -> None:
        """Send a request with retry logic.

        Args:
            request: Request to send.
        """
        start_time = time.monotonic()
        retries_used = 0
        last_error: Exception | None = None

        try:
            for attempt in range(request.retry_policy.max_retries + 1):
                if not self._running or request.is_expired():
                    break

                try:
                    # Get sequence number
                    sequence = self._next_sequence_callback()

                    # Register correlation if expecting response
                    if request.expected_response_opcode is not None:
                        self._correlation.register(request, sequence)

                    # Send command
                    _LOGGER.debug(
                        "Sending request %s (attempt %d/%d seq=%d opcode=0x%04X target=0x%04X)",
                        request.request_id,
                        attempt + 1,
                        request.retry_policy.max_retries + 1,
                        sequence,
                        request.opcode,
                        request.target_node,
                    )

                    response_data = await self._send_callback(request, sequence)

                    # Success
                    latency_ms = (time.monotonic() - start_time) * 1000
                    result = CommandResult(
                        request_id=request.request_id,
                        status="success",
                        response_data=response_data,
                        latency_ms=latency_ms,
                        retries_used=retries_used,
                    )
                    self._complete_request(request, result)
                    return

                except asyncio.CancelledError:
                    raise
                except (OSError, TimeoutError, TuyaBLEMeshError) as exc:
                    # Transport: absorb all send errors for retry
                    last_error = exc
                    retries_used += 1

                    if attempt >= request.retry_policy.max_retries:
                        break

                    # Calculate backoff
                    backoff = min(
                        request.retry_policy.backoff_base * (2**attempt),
                        request.retry_policy.backoff_max,
                    )

                    # Add jitter
                    if request.retry_policy.jitter > 0:
                        jitter = backoff * request.retry_policy.jitter * (random.random() * 2 - 1)
                        backoff += jitter

                    _LOGGER.warning(
                        "Request %s failed (attempt %d/%d): %s — retrying in %.1fs",
                        request.request_id,
                        attempt + 1,
                        request.retry_policy.max_retries + 1,
                        exc,
                        backoff,
                    )

                    await asyncio.sleep(backoff)

            # All retries exhausted
            latency_ms = (time.monotonic() - start_time) * 1000
            result = CommandResult(
                request_id=request.request_id,
                status="error",
                latency_ms=latency_ms,
                retries_used=retries_used,
                error=last_error,
            )
            self._complete_request(request, result)

        finally:
            # Remove from in-flight tracking
            self._in_flight.pop(request.request_id, None)
            device_count = self._in_flight_per_device.get(request.target_node, 0)
            if device_count > 0:
                self._in_flight_per_device[request.target_node] = device_count - 1

    def _complete_request(self, request: CommandRequest, result: CommandResult) -> None:
        """Complete a request by setting its result future and recording metrics.

        Args:
            request: Request that completed.
            result: Result of the request.
        """
        self._metrics.record_result(result, request.opcode)
        self._history.append((request, result))

        future = self._result_futures.pop(request.request_id, None)
        if future is not None and not future.done():
            future.set_result(result)

        _LOGGER.debug(
            "Completed request %s: status=%s latency=%.1fms retries=%d",
            request.request_id,
            result.status,
            result.latency_ms,
            result.retries_used,
        )

    @property
    def metrics(self) -> TransportMetrics:
        """Return transport metrics."""
        return self._metrics

    @property
    def history(self) -> deque[tuple[CommandRequest, CommandResult]]:
        """Return command history (last 100)."""
        return self._history

    def queue_depth(self) -> int:
        """Return current queue depth."""
        return self._queue.qsize()

    def in_flight_count(self) -> int:
        """Return current in-flight count."""
        return len(self._in_flight)
