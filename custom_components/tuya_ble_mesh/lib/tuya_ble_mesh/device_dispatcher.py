"""Async command dispatcher for BLE mesh device.

Provides fire-and-forget enqueue-and-return semantics with TTL and retry logic.
The dispatcher runs a separate asyncio worker task that drains the internal
queue, respects TTL, and handles retry/cancellation/reconnect.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING

from tuya_ble_mesh.const import CONNECTION_EVENT_WAIT_TIMEOUT
from tuya_ble_mesh.exceptions import (
    CommandQueueFullError,
    DisconnectedError,
    MeshConnectionError,
)

if TYPE_CHECKING:
    from tuya_ble_mesh.device import MeshDevice

_LOGGER = logging.getLogger(__name__)

# Command queue limits
_QUEUE_MAX_SIZE = 32
_COMMAND_TTL = 60.0  # seconds
_QUEUE_POLL_INTERVAL = 1.0  # seconds between queue.get() polls (allows checking _running)


class _QueuedCommand:
    """A command waiting in the queue."""

    __slots__ = ("created_at", "dest_id", "opcode", "params")

    def __init__(
        self,
        opcode: int,
        params: bytes,
        dest_id: int,
    ) -> None:
        """Initialize a queued command.

        Args:
            opcode: Telink command code.
            params: Command parameters.
            dest_id: Target mesh address.
        """
        self.opcode = opcode
        self.params = params
        self.dest_id = dest_id
        self.created_at = time.monotonic()


class _CommandDispatcher:
    """Async command dispatcher with internal worker task.

    Provides fire-and-forget enqueue-and-return semantics for HA callers.
    The dispatcher runs a separate asyncio worker task that drains the internal
    queue, respects TTL, and handles retry/cancellation/reconnect.
    """

    def __init__(
        self,
        device: MeshDevice,
        max_size: int = _QUEUE_MAX_SIZE,
        ttl: float = _COMMAND_TTL,
    ) -> None:
        """Initialize the command dispatcher.

        Args:
            device: Parent MeshDevice instance.
            max_size: Maximum queue size.
            ttl: Command time-to-live in seconds.
        """
        self._device = device
        self._max_size = max_size
        self._ttl = ttl
        self._queue: asyncio.Queue[_QueuedCommand] = asyncio.Queue(maxsize=max_size)
        self._worker_task: asyncio.Task[None] | None = None
        self._running = False

    def start(self) -> None:
        """Start the dispatcher worker task."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        self._worker_task.add_done_callback(self._log_worker_exception)
        _LOGGER.debug("Command dispatcher started")

    @staticmethod
    def _log_worker_exception(task: asyncio.Task[None]) -> None:
        """Log unhandled exceptions from the worker task."""
        if task.cancelled():
            return
        try:
            exc = task.exception()
            if exc is not None:
                _LOGGER.error("Command dispatcher worker crashed: %s", exc, exc_info=exc)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Stop the dispatcher worker task and cancel pending commands."""
        if not self._running:
            return
        self._running = False
        if self._worker_task is not None:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        # Drain any remaining items from the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        _LOGGER.debug("Command dispatcher stopped")

    async def enqueue(self, opcode: int, params: bytes, dest_id: int) -> None:
        """Enqueue a command for async sending (fire-and-forget).

        Args:
            opcode: Telink command code.
            params: Command parameters.
            dest_id: Target mesh address.

        Raises:
            CommandQueueFullError: If queue is at capacity.
        """
        if self._queue.full():
            msg = f"Command queue full ({self._max_size})"
            raise CommandQueueFullError(msg)
        cmd = _QueuedCommand(opcode, params, dest_id)
        await self._queue.put(cmd)
        _LOGGER.debug("Queued command 0x%02X (queue size: %d)", opcode, self._queue.qsize())

    async def _worker(self) -> None:
        """Worker task that drains the queue and sends commands."""
        _LOGGER.debug("Command dispatcher worker started")
        while self._running:
            try:
                # Wait for a command with a short timeout to allow checking _running
                try:
                    cmd = await asyncio.wait_for(self._queue.get(), timeout=_QUEUE_POLL_INTERVAL)
                except TimeoutError:
                    continue

                # Check TTL
                age = time.monotonic() - cmd.created_at
                if age > self._ttl:
                    _LOGGER.warning(
                        "Command 0x%02X expired (age=%.1fs, TTL=%.1fs), dropping",
                        cmd.opcode,
                        age,
                        self._ttl,
                    )
                    self._queue.task_done()
                    continue

                # Wait for device to be ready (event-driven, no busy-wait)
                if not self._device.is_connected:
                    try:
                        # Wait for connection with periodic checks of _running flag
                        while self._running and not self._device.is_connected:
                            try:
                                await asyncio.wait_for(
                                    self._device._connected_event.wait(),
                                    timeout=CONNECTION_EVENT_WAIT_TIMEOUT,
                                )
                            except TimeoutError:
                                # Timeout allows checking _running flag periodically
                                continue
                    except asyncio.CancelledError:
                        self._queue.task_done()
                        raise

                if not self._running:
                    self._queue.task_done()
                    break

                # Send the command
                try:
                    await self._device._send_now(cmd.opcode, cmd.params, cmd.dest_id)
                except (MeshConnectionError, DisconnectedError):
                    _LOGGER.warning(
                        "Command 0x%02X send failed, dropping",
                        cmd.opcode,
                        exc_info=True,
                    )

                self._queue.task_done()

            except asyncio.CancelledError:
                _LOGGER.debug("Command dispatcher worker cancelled")
                raise
            except Exception:
                _LOGGER.error("Command dispatcher worker error", exc_info=True)
                await asyncio.sleep(1)  # Backoff before retrying loop

        _LOGGER.debug("Command dispatcher worker stopped")
