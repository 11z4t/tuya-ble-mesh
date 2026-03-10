"""Reconnection strategy with exponential backoff for BLE mesh devices.

Handles connection retry logic with exponential backoff, storm detection,
and device-specific parameters (shorter backoff for HTTP bridges).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field

_LOGGER = logging.getLogger(__name__)


@dataclass
class ReconnectionStatistics:
    """Statistics for reconnection attempts and storm detection."""

    total_reconnects: int = 0
    consecutive_failures: int = 0
    reconnect_times: deque[float] = field(default_factory=lambda: deque(maxlen=50))
    storm_detected: bool = False
    last_reconnect_time: float | None = None


class ReconnectionStrategy:
    """Manages reconnection attempts with exponential backoff.

    Supports:
    - Exponential backoff with configurable min/max/multiplier
    - Device-specific backoff (shorter for HTTP bridges)
    - Storm detection (too many reconnects in a time window)
    - Max failure limit (optional)
    """

    def __init__(
        self,
        *,
        initial_backoff: float = 5.0,
        max_backoff: float = 300.0,
        multiplier: float = 2.0,
        bridge_initial_backoff: float = 3.0,
        bridge_max_backoff: float = 120.0,
        storm_window_seconds: int = 300,
        storm_threshold: int = 10,
        max_failures: int = 0,  # 0 = unlimited
    ) -> None:
        """Initialize reconnection strategy.

        Args:
            initial_backoff: Initial backoff delay in seconds (BLE devices).
            max_backoff: Maximum backoff delay in seconds (BLE devices).
            multiplier: Backoff multiplier after each failure.
            bridge_initial_backoff: Initial backoff for bridge devices.
            bridge_max_backoff: Maximum backoff for bridge devices.
            storm_window_seconds: Time window for storm detection.
            storm_threshold: Number of reconnects in window to trigger storm.
            max_failures: Maximum consecutive failures before giving up (0=unlimited).
        """
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._multiplier = multiplier
        self._bridge_initial_backoff = bridge_initial_backoff
        self._bridge_max_backoff = bridge_max_backoff
        self._storm_window_seconds = storm_window_seconds
        self._storm_threshold = storm_threshold
        self._max_failures = max_failures

        self._current_backoff = initial_backoff
        self._stats = ReconnectionStatistics()

    @property
    def statistics(self) -> ReconnectionStatistics:
        """Return reconnection statistics."""
        return self._stats

    @property
    def current_backoff(self) -> float:
        """Return current backoff delay in seconds."""
        return self._current_backoff

    @property
    def consecutive_failures(self) -> int:
        """Return number of consecutive failures."""
        return self._stats.consecutive_failures

    def reset(self, *, is_bridge: bool = False) -> None:
        """Reset backoff to initial value after successful connection.

        Args:
            is_bridge: True if device uses HTTP bridge (shorter backoff).
        """
        self._current_backoff = (
            self._bridge_initial_backoff if is_bridge else self._initial_backoff
        )
        self._stats.consecutive_failures = 0
        self._stats.storm_detected = False

    def record_failure(self, *, is_bridge: bool = False) -> None:
        """Record a failed reconnection attempt and increase backoff.

        Args:
            is_bridge: True if device uses HTTP bridge (shorter max backoff).
        """
        self._stats.consecutive_failures += 1
        self._stats.reconnect_times.append(time.time())

        max_backoff = self._bridge_max_backoff if is_bridge else self._max_backoff
        self._current_backoff = min(self._current_backoff * self._multiplier, max_backoff)

    def record_success(self) -> None:
        """Record a successful reconnection."""
        self._stats.total_reconnects += 1
        self._stats.last_reconnect_time = time.time()
        self._stats.reconnect_times.append(time.time())

    def check_storm(self) -> bool:
        """Check if reconnect attempts indicate a storm (tight loop).

        Returns:
            True if storm detected (too many reconnects in time window).
        """
        now = time.time()
        cutoff = now - self._storm_window_seconds

        # Prune old reconnect times outside the window
        while self._stats.reconnect_times and self._stats.reconnect_times[0] < cutoff:
            self._stats.reconnect_times.popleft()

        is_storm = len(self._stats.reconnect_times) >= self._storm_threshold
        if is_storm and not self._stats.storm_detected:
            self._stats.storm_detected = True
            _LOGGER.warning(
                "Reconnect storm detected: %d attempts in %d seconds",
                len(self._stats.reconnect_times),
                self._storm_window_seconds,
            )

        return is_storm

    def should_give_up(self) -> bool:
        """Check if max failure limit has been reached.

        Returns:
            True if should stop reconnecting (max failures reached).
        """
        if self._max_failures == 0:
            return False  # Unlimited retries

        return self._stats.consecutive_failures >= self._max_failures

    async def wait_before_retry(self, *, is_bridge: bool = False) -> None:
        """Wait for the current backoff period before next retry.

        Args:
            is_bridge: True if device uses HTTP bridge (affects logging).
        """
        _LOGGER.info(
            "Reconnecting in %.0fs (attempt %d%s)",
            self._current_backoff,
            self._stats.consecutive_failures + 1,
            ", bridge" if is_bridge else "",
        )
        await asyncio.sleep(self._current_backoff)
