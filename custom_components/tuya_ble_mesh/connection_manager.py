"""Connection lifecycle manager for Tuya BLE Mesh devices.

Handles connect, disconnect, reconnect with exponential backoff,
error classification, repair issue creation, RSSI polling, and
command retry. Extracted from coordinator.py (PLAT-667).
"""

from __future__ import annotations

import asyncio
import logging
import statistics
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from custom_components.tuya_ble_mesh.error_classifier import (
    ErrorClass,
    classify_error,
)

# Re-export for backward compatibility
__all__ = ["ConnectionManager", "ErrorClass", "classify_error"]

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from custom_components.tuya_ble_mesh.coordinator import AnyMeshDevice

_LOGGER = logging.getLogger(__name__)

# --- Reconnect backoff parameters ---
DEBOUNCE_DELAY = 1.5  # PLAT-754: Initial delay before first reconnect attempt
INITIAL_BACKOFF = 5.0
MAX_BACKOFF = 300.0
BACKOFF_MULTIPLIER = 2.0

# Bridge-specific (shorter backoff for HTTP bridges)
BRIDGE_INITIAL_BACKOFF = 3.0
BRIDGE_MAX_BACKOFF = 120.0

# Reconnect storm detection
STORM_WINDOW_SECONDS = 300  # 5 minutes
STORM_DEFAULT_THRESHOLD = 10

# Max consecutive reconnect failures before giving up (0 = unlimited)
DEFAULT_MAX_RECONNECT_FAILURES = 0

# RSSI refresh interval (seconds) — adaptive
RSSI_MIN_INTERVAL = 30.0
RSSI_MAX_INTERVAL = 300.0
RSSI_DEFAULT_INTERVAL = 60.0
RSSI_STABILITY_THRESHOLD = 3

# Rate limiting: cap concurrent BLE commands per device
COMMAND_CONCURRENCY_LIMIT = 5

# Reconnect timeline max events
RECONNECT_TIMELINE_MAX = 20


@dataclass
class ReconnectEvent:
    """A single reconnect attempt record for timeline diagnostics.

    Stored in ConnectionStatistics.reconnect_timeline (last 20 events).
    Used by diagnostics to explain *when* and *why* the device went offline.
    """

    timestamp: float  # Unix time of the attempt
    error_class: str  # ErrorClass.value (e.g. "transient", "mesh_auth")
    backoff: float  # Backoff delay (seconds) applied *before* this attempt
    attempt: int  # Consecutive failure count at time of this event


__all__ = ["ErrorClass", "classify_error"]  # Re-exported from error_classifier


@dataclass
class ConnectionStatistics:
    """Connection and performance statistics for diagnostics."""

    connect_time: float | None = None
    total_reconnects: int = 0
    total_errors: int = 0
    connection_errors: int = 0
    command_errors: int = 0
    response_times: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    last_error: str | None = None
    last_error_time: float | None = None
    last_error_class: str = ErrorClass.UNKNOWN.value
    connection_uptime: float = 0.0
    last_disconnect_time: float | None = None
    avg_response_time: float = 0.0
    reconnect_times: deque[float] = field(default_factory=lambda: deque(maxlen=50))
    storm_detected: bool = False
    reconnect_timeline: deque[ReconnectEvent] = field(
        default_factory=lambda: deque(maxlen=RECONNECT_TIMELINE_MAX)
    )
    rssi_history: deque[tuple[float, int]] = field(default_factory=lambda: deque(maxlen=50))


class ConnectionManager:
    """Manages connect/disconnect/reconnect lifecycle for a mesh device.

    Owns connection statistics, error classification, repair issues,
    RSSI polling, and command retry logic. The coordinator delegates
    all connection operations here.

    Args:
        device: The underlying mesh device.
        hass: Home Assistant instance (None in standalone/test mode).
        entry_id: Config entry ID for repair issues.
        entry_name: Human-readable name for repair issue placeholders.
        on_connected: Callback(response_time) when connection succeeds.
        on_disconnected: Callback() when connection is lost.
        on_state_update: Callback() to dispatch state updates to HA/listeners.
    """

    def __init__(
        self,
        device: AnyMeshDevice,
        *,
        hass: HomeAssistant | None = None,
        entry_id: str | None = None,
        entry_name: str = "",
        on_connected: Callable[[float], None] | None = None,
        on_disconnected: Callable[[], None] | None = None,
        on_state_update: Callable[[], None] | None = None,
    ) -> None:
        self._device = device
        self._hass = hass
        self._entry_id = entry_id
        self.entry_name = entry_name

        # Callbacks to coordinator
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_state_update = on_state_update

        # Connection state
        self._running = False
        self._backoff = INITIAL_BACKOFF
        self._reconnect_task: asyncio.Task[None] | None = None
        self._rssi_task: asyncio.Task[None] | None = None

        # Statistics
        self._stats = ConnectionStatistics()

        # Storm & failure tracking
        self._storm_threshold: int = STORM_DEFAULT_THRESHOLD
        self._max_reconnect_failures: int = DEFAULT_MAX_RECONNECT_FAILURES
        self._consecutive_failures: int = 0
        self._raised_repair_issues: set[str] = set()

        # Adaptive RSSI polling
        self._rssi_interval = RSSI_DEFAULT_INTERVAL
        self._state_change_counter = 0
        self._stable_cycles = 0
        self._latest_rssi: int | None = None

        # Command concurrency
        self._command_semaphore = asyncio.Semaphore(COMMAND_CONCURRENCY_LIMIT)

    # --- Properties ---

    @property
    def statistics(self) -> ConnectionStatistics:
        """Return connection and performance statistics."""
        return self._stats

    @property
    def consecutive_failures(self) -> int:
        """Number of consecutive reconnect failures since last successful connect."""
        return self._consecutive_failures

    @property
    def storm_threshold(self) -> int:
        """Reconnect-storm detection threshold."""
        return self._storm_threshold

    @property
    def backoff(self) -> float:
        """Current reconnect backoff delay in seconds."""
        return self._backoff

    @backoff.setter
    def backoff(self, value: float) -> None:
        """Set reconnect backoff delay."""
        self._backoff = value

    @property
    def running(self) -> bool:
        """Whether the connection manager is actively running."""
        return self._running

    @running.setter
    def running(self, value: bool) -> None:
        """Set running state."""
        self._running = value

    def avg_response_time_ms(self) -> float | None:
        """Return mean connection response time in milliseconds, or None if no data."""
        if not self._stats.response_times:
            return None
        return statistics.mean(self._stats.response_times) * 1000

    # --- Connect / Disconnect ---

    async def async_connect(self) -> float:
        """Connect to the device.

        Returns:
            Response time in seconds.

        Raises:
            Any exception from device.connect().
        """
        start_time = time.monotonic()
        await self._device.connect()
        response_time = time.monotonic() - start_time
        self._stats.response_times.append(response_time)
        self._stats.connect_time = time.time()
        self._backoff = BRIDGE_INITIAL_BACKOFF if self.is_bridge_device() else INITIAL_BACKOFF
        # PLAT-695: Populate RSSI immediately after connect (from BleakClient.rssi)
        rssi = getattr(self._device, "rssi", None)
        if rssi is not None:
            self._latest_rssi = rssi
            self._stats.rssi_history.append((time.time(), rssi))
        self.start_rssi_polling()
        self._log_connect_metrics(response_time)
        return response_time

    async def async_disconnect(self) -> None:
        """Disconnect from the device, ignoring errors."""
        try:
            await self._device.disconnect()
        except Exception:
            _LOGGER.debug("Disconnect error during stop (ignored)", exc_info=True)

    def handle_disconnect(self) -> None:
        """Handle device disconnect — update stats and schedule reconnect.

        Called by the coordinator's _on_disconnect callback.
        """
        _LOGGER.warning("Device disconnected: %s", self._device.address)

        # Update connection statistics
        if self._stats.connect_time is not None:
            uptime = time.time() - self._stats.connect_time
            self._stats.connection_uptime += uptime
        self._stats.last_disconnect_time = time.time()

        if self._stats.response_times:
            self._stats.avg_response_time = sum(self._stats.response_times) / len(
                self._stats.response_times
            )

        self.stop_rssi_polling()

        # Use bridge-specific backoff if this is a bridge device
        if self.is_bridge_device():
            self._backoff = BRIDGE_INITIAL_BACKOFF

        self.schedule_reconnect()

    def record_connection_error(self, err: Exception) -> None:
        """Record a connection error in statistics.

        Args:
            err: The exception that occurred.
        """
        self._stats.total_errors += 1
        self._stats.connection_errors += 1
        self._stats.last_error = str(err)
        self._stats.last_error_time = time.time()

    # --- Reconnect ---

    def schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt with exponential backoff."""
        if not self._running:
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        _ = loop  # used implicitly by create_task

        if self._reconnect_task is not None:
            self._reconnect_task.cancel()

        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        self._reconnect_task.add_done_callback(self._log_task_exception)

    async def _reconnect_loop(self) -> None:
        """Attempt reconnection with exponential backoff.

        On success: clears connectivity repair issues, resets failure counter,
        marks entities available (triggers HA state update via callback).
        On failure: classifies error, checks for storm, creates repair if needed.

        PLAT-754: Initial debounce delay to avoid immediate reconnect
        loops during transient disconnects.
        """
        # PLAT-754: Debounce delay before first reconnect attempt
        await asyncio.sleep(DEBOUNCE_DELAY)

        if not self._running:
            return

        is_bridge = self.is_bridge_device()
        max_backoff = BRIDGE_MAX_BACKOFF if is_bridge else MAX_BACKOFF

        while self._running:
            # Check max reconnect failure limit
            if (
                self._max_reconnect_failures > 0
                and self._consecutive_failures >= self._max_reconnect_failures
            ):
                _LOGGER.error(
                    "Max reconnect failures (%d) reached for %s — giving up",
                    self._max_reconnect_failures,
                    self._device.address,
                )
                if self._on_state_update:
                    self._on_state_update()
                return

            _LOGGER.info(
                "Reconnecting to %s in %.0fs (attempt %d%s)",
                self._device.address,
                self._backoff,
                self._consecutive_failures + 1,
                ", bridge" if is_bridge else "",
            )
            await asyncio.sleep(self._backoff)

            if not self._running:
                break

            try:
                start_time = time.monotonic()
                await self._device.connect()
                response_time = time.monotonic() - start_time
                self._stats.response_times.append(response_time)
                self._stats.connect_time = time.time()
                self._stats.total_reconnects += 1
                self._stats.reconnect_times.append(time.time())
                self._backoff = BRIDGE_INITIAL_BACKOFF if is_bridge else INITIAL_BACKOFF
                self._consecutive_failures = 0
                self._stats.storm_detected = False
                # PLAT-695: Populate RSSI immediately after reconnect (from BleakClient.rssi)
                rssi = getattr(self._device, "rssi", None)
                if rssi is not None:
                    self._latest_rssi = rssi
                    self._stats.rssi_history.append((time.time(), rssi))
                _LOGGER.info("Reconnected to %s (%.2fs)", self._device.address, response_time)
                self._log_connect_metrics(response_time)
                self._clear_repair_issues_on_recovery()

                if self._on_connected:
                    self._on_connected(response_time)
                else:
                    self.start_rssi_polling()
                return
            except Exception as err:
                self._stats.total_errors += 1
                self._stats.connection_errors += 1
                self._stats.last_error = str(err)
                self._stats.last_error_time = time.time()
                self._consecutive_failures += 1
                error_class = self.classify_error(err)
                self._stats.last_error_class = error_class.value
                self._stats.reconnect_times.append(time.time())

                _LOGGER.warning(
                    "Reconnect failed for %s (class=%s, consecutive=%d)",
                    self._device.address,
                    error_class.value,
                    self._consecutive_failures,
                    exc_info=True,
                )

                # Permanent errors should not retry
                if error_class == ErrorClass.PERMANENT:
                    _LOGGER.error(
                        "Permanent error for %s — stopping reconnect",
                        self._device.address,
                    )
                    if self._on_state_update:
                        self._on_state_update()
                    return

                self._maybe_create_repair_issue(error_class)

                # Detect reconnect storm
                if (
                    self._hass is not None
                    and self._entry_id is not None
                    and self._check_reconnect_storm()
                    and not self._stats.storm_detected
                ):
                    self._stats.storm_detected = True
                    from custom_components.tuya_ble_mesh.repairs import (
                        async_create_issue_reconnect_storm,
                    )

                    storm_task = asyncio.create_task(
                        async_create_issue_reconnect_storm(
                            self._hass,
                            self.entry_name or self._device.address,
                            len(self._stats.reconnect_times),
                            self._entry_id,
                            STORM_WINDOW_SECONDS // 60,
                        )
                    )
                    storm_task.add_done_callback(self._log_task_exception)

                # Record timeline event
                self._stats.reconnect_timeline.append(
                    ReconnectEvent(
                        timestamp=time.time(),
                        error_class=error_class.value,
                        backoff=self._backoff,
                        attempt=self._consecutive_failures,
                    )
                )

                self._backoff = min(self._backoff * BACKOFF_MULTIPLIER, max_backoff)

                if self._on_state_update:
                    self._on_state_update()

    # --- Error Classification ---

    def classify_error(self, err: Exception) -> ErrorClass:
        """Classify a connection error into a category for repair creation.

        Delegates to the standalone ``classify_error()`` function in
        ``error_classifier.py`` (extracted in PLAT-668).
        """
        return classify_error(err)

    # --- Repair Issues ---

    def _maybe_create_repair_issue(self, error_class: ErrorClass) -> None:
        """Create a repair issue for the given error class, at most once per recovery."""
        if self._hass is None or self._entry_id is None:
            return

        from custom_components.tuya_ble_mesh.repairs import (
            ISSUE_AUTH_OR_MESH_MISMATCH,
            ISSUE_BRIDGE_UNREACHABLE,
            ISSUE_DEVICE_NOT_FOUND,
            ISSUE_TIMEOUT,
            async_create_issue_auth_or_mesh_mismatch,
            async_create_issue_bridge_unreachable,
            async_create_issue_device_not_found,
            async_create_issue_timeout,
        )

        _CLASS_TO_ISSUE = {
            ErrorClass.BRIDGE_DOWN: ISSUE_BRIDGE_UNREACHABLE,
            ErrorClass.MESH_AUTH: ISSUE_AUTH_OR_MESH_MISMATCH,
            ErrorClass.DEVICE_OFFLINE: ISSUE_DEVICE_NOT_FOUND,
            ErrorClass.TRANSIENT: ISSUE_TIMEOUT,
        }
        issue_base = _CLASS_TO_ISSUE.get(error_class)
        if issue_base is None:
            return

        if issue_base in self._raised_repair_issues:
            return

        self._raised_repair_issues.add(issue_base)
        name = self.entry_name or self._device.address
        host = getattr(self._device, "host", "") or ""
        port = getattr(self._device, "port", 0) or 0
        hass = self._hass
        entry_id = self._entry_id
        mac = self._device.address

        async def _create() -> None:
            if error_class == ErrorClass.BRIDGE_DOWN:
                await async_create_issue_bridge_unreachable(hass, host, port, entry_id)
            elif error_class == ErrorClass.MESH_AUTH:
                await async_create_issue_auth_or_mesh_mismatch(hass, name, entry_id)
            elif error_class == ErrorClass.DEVICE_OFFLINE:
                await async_create_issue_device_not_found(hass, name, mac, entry_id)
            elif error_class == ErrorClass.TRANSIENT:
                await async_create_issue_timeout(hass, name, entry_id)

        task = asyncio.create_task(_create())
        task.add_done_callback(self._log_task_exception)

    def _clear_repair_issues_on_recovery(self) -> None:
        """Clear all connection repair issues after successful reconnect."""
        if self._hass is None or self._entry_id is None:
            return

        from custom_components.tuya_ble_mesh.repairs import (
            ISSUE_AUTH_OR_MESH_MISMATCH,
            ISSUE_BRIDGE_UNREACHABLE,
            ISSUE_DEVICE_NOT_FOUND,
            ISSUE_RECONNECT_STORM,
            ISSUE_TIMEOUT,
            async_delete_issue,
        )

        for base_id in (
            ISSUE_BRIDGE_UNREACHABLE,
            ISSUE_AUTH_OR_MESH_MISMATCH,
            ISSUE_DEVICE_NOT_FOUND,
            ISSUE_TIMEOUT,
            ISSUE_RECONNECT_STORM,
        ):
            async_delete_issue(self._hass, base_id, self._entry_id)
        self._raised_repair_issues.clear()

    def _check_reconnect_storm(self) -> bool:
        """Return True if reconnect attempts indicate a storm."""
        now = time.time()
        cutoff = now - STORM_WINDOW_SECONDS
        while self._stats.reconnect_times and self._stats.reconnect_times[0] < cutoff:
            self._stats.reconnect_times.popleft()
        return len(self._stats.reconnect_times) >= self._storm_threshold

    # --- RSSI Polling ---

    def is_bridge_device(self) -> bool:
        """Return True if device communicates via HTTP bridge (no local BLE)."""
        type_name = type(self._device).__name__
        return "Bridge" in type_name

    def start_rssi_polling(self) -> None:
        """Start periodic RSSI refresh via BLE scan."""
        if self.is_bridge_device():
            return
        self.stop_rssi_polling()
        try:
            self._rssi_task = asyncio.create_task(self._rssi_loop())
            self._rssi_task.add_done_callback(self._log_task_exception)
        except RuntimeError:
            pass

    def stop_rssi_polling(self) -> None:
        """Stop RSSI polling."""
        if self._rssi_task is not None:
            self._rssi_task.cancel()
            self._rssi_task = None

    async def _rssi_loop(self) -> None:
        """Periodically update RSSI using HA's bluetooth integration or BleakScanner."""
        try:
            while self._running:
                await asyncio.sleep(self._rssi_interval)
                if not self._running:
                    break

                try:
                    ble_device = None

                    if self._hass is not None:
                        from homeassistant.components.bluetooth import (
                            async_ble_device_from_address,
                        )

                        ble_device = async_ble_device_from_address(
                            self._hass, self._device.address, connectable=False
                        )
                    else:
                        from bleak import BleakScanner

                        ble_device = await BleakScanner.find_device_by_address(
                            self._device.address, timeout=10.0
                        )

                    if ble_device is not None and ble_device.rssi is not None:
                        rssi = ble_device.rssi
                        self._stats.rssi_history.append((time.time(), rssi))
                        # Return RSSI to coordinator via callback with special marker
                        if self._on_state_update:
                            # Store latest RSSI so coordinator can read it
                            self._latest_rssi = rssi
                            self._on_state_update()

                        # Track stability
                        if len(self._stats.rssi_history) >= 2:
                            prev_rssi = self._stats.rssi_history[-2][1]
                            if abs(rssi - prev_rssi) <= 2:
                                self._stable_cycles += 1
                                if self._stable_cycles >= RSSI_STABILITY_THRESHOLD:
                                    self.adjust_polling_interval()
                            else:
                                self._stable_cycles = 0

                except Exception:
                    _LOGGER.debug("RSSI update failed (ignored)", exc_info=True)
        except asyncio.CancelledError:
            pass

    def adjust_polling_interval(self) -> None:
        """Adjust RSSI polling interval based on state change frequency."""
        if self._state_change_counter >= 2:
            self._rssi_interval = max(
                RSSI_MIN_INTERVAL,
                self._rssi_interval * 0.75,
            )
            _LOGGER.debug(
                "Adaptive polling: frequent changes, interval=%.1fs",
                self._rssi_interval,
            )
        elif self._stable_cycles >= RSSI_STABILITY_THRESHOLD:
            self._rssi_interval = min(
                RSSI_MAX_INTERVAL,
                self._rssi_interval * 1.5,
            )
            _LOGGER.debug(
                "Adaptive polling: stable state, interval=%.1fs",
                self._rssi_interval,
            )

        self._state_change_counter = 0

    def record_state_change(self) -> None:
        """Record a state change for adaptive polling."""
        self._state_change_counter += 1
        self._stable_cycles = 0
        self.adjust_polling_interval()

    @property
    def latest_rssi(self) -> int | None:
        """Return the latest RSSI value from polling, or None."""
        return getattr(self, "_latest_rssi", None)

    # --- Command Retry ---

    async def send_command_with_retry(
        self,
        coro_func: Callable[[], Any],
        *,
        max_retries: int | None = None,
        base_delay: float | None = None,
        description: str = "command",
    ) -> None:
        """Execute a device command coroutine with exponential-backoff retry.

        Args:
            coro_func: Callable that returns a coroutine.
            max_retries: Override for maximum retry attempts.
            base_delay: Override for base retry delay in seconds.
            description: Human-readable label for log messages.

        Raises:
            The last exception if all retries are exhausted.
        """
        from custom_components.tuya_ble_mesh.const import (
            DEFAULT_COMMAND_RETRY_BASE_DELAY,
            DEFAULT_MAX_COMMAND_RETRIES,
        )

        _max = max_retries if max_retries is not None else DEFAULT_MAX_COMMAND_RETRIES
        _delay = base_delay if base_delay is not None else DEFAULT_COMMAND_RETRY_BASE_DELAY

        async with self._command_semaphore:
            last_exc: Exception | None = None
            for attempt in range(1, _max + 1):
                try:
                    await coro_func()
                    return
                except Exception as exc:
                    last_exc = exc
                    self._stats.command_errors += 1
                    self._stats.total_errors += 1
                    if attempt < _max:
                        wait = _delay * (2 ** (attempt - 1))
                        _LOGGER.warning(
                            "BLE command '%s' failed for %s (attempt %d/%d) — "
                            "retrying in %.1fs: %s",
                            description,
                            self._device.address,
                            attempt,
                            _max,
                            wait,
                            exc,
                        )
                        await asyncio.sleep(wait)
                    else:
                        _LOGGER.error(
                            "BLE command '%s' failed for %s after %d attempts: %s",
                            description,
                            self._device.address,
                            _max,
                            exc,
                        )

            if last_exc is not None:
                raise last_exc

    # --- Helpers ---

    def _log_connect_metrics(self, response_time: float) -> None:
        """Log connection performance metrics at INFO level."""
        avg_ms = self.avg_response_time_ms()
        if avg_ms is not None:
            _LOGGER.info(
                "Connect metrics for %s: this=%.0fms avg=%.0fms reconnects=%d errors=%d",
                self._device.address,
                response_time * 1000,
                avg_ms,
                self._stats.total_reconnects,
                self._stats.total_errors,
            )
        else:
            _LOGGER.info(
                "Connect metrics for %s: this=%.0fms (first connection)",
                self._device.address,
                response_time * 1000,
            )

    def _log_task_exception(self, task: asyncio.Task) -> None:  # type: ignore[type-arg]
        """Log exceptions from background tasks."""
        if task.cancelled():
            return
        try:
            exc = task.exception()
            if exc is not None:
                _LOGGER.error(
                    "Background task failed for %s",
                    self._device.address,
                    exc_info=exc,
                )
        except Exception:
            _LOGGER.debug("Failed to inspect task exception", exc_info=True)

    async def async_cancel_tasks(self) -> None:
        """Cancel all background tasks and await their completion."""
        tasks_to_cancel: list[asyncio.Task[None]] = []
        if self._rssi_task is not None:
            self._rssi_task.cancel()
            tasks_to_cancel.append(self._rssi_task)
            self._rssi_task = None
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            tasks_to_cancel.append(self._reconnect_task)
            self._reconnect_task = None

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
