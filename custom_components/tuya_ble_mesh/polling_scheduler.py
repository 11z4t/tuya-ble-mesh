"""Adaptive polling scheduler for RSSI updates.

Adjusts polling interval based on state change frequency:
- Faster polling when values change frequently (more responsive)
- Slower polling when stable (lower overhead)
"""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)


class PollingScheduler:
    """Adaptive polling scheduler for RSSI updates.

    Tracks state change frequency and adjusts polling interval dynamically
    to balance responsiveness (fast updates when active) with efficiency
    (slow polling when idle).
    """

    def __init__(
        self,
        *,
        min_interval: float = 30.0,
        max_interval: float = 300.0,
        default_interval: float = 60.0,
        stability_threshold: int = 3,
    ) -> None:
        """Initialize polling scheduler.

        Args:
            min_interval: Minimum polling interval in seconds (frequent changes).
            max_interval: Maximum polling interval in seconds (stable state).
            default_interval: Initial/fallback polling interval.
            stability_threshold: Number of stable cycles before increasing interval.
        """
        self._min_interval = min_interval
        self._max_interval = max_interval
        self._default_interval = default_interval
        self._stability_threshold = stability_threshold

        self._current_interval = default_interval
        self._state_change_counter = 0
        self._stable_cycles = 0

    @property
    def current_interval(self) -> float:
        """Return current polling interval in seconds."""
        return self._current_interval

    def reset(self) -> None:
        """Reset scheduler to default state."""
        self._current_interval = self._default_interval
        self._state_change_counter = 0
        self._stable_cycles = 0

    def record_change(self) -> None:
        """Record a state change (triggers faster polling)."""
        self._state_change_counter += 1
        self._stable_cycles = 0

    def record_stable_cycle(self) -> None:
        """Record a stable polling cycle (no significant change)."""
        self._stable_cycles += 1

    def adjust_interval(self) -> None:
        """Adjust polling interval based on recent state changes.

        Decreases interval when frequent changes detected (≥2 changes),
        increases interval when stable for multiple cycles.
        """
        # Frequent changes = shorter interval (more responsive)
        if self._state_change_counter >= 2:
            new_interval = max(
                self._min_interval,
                self._current_interval * 0.75,  # Decrease by 25%
            )
            if new_interval != self._current_interval:
                _LOGGER.debug(
                    "Adaptive polling: frequent changes detected, interval=%.1fs",
                    new_interval,
                )
                self._current_interval = new_interval

        # Stable state = longer interval (lower overhead)
        elif self._stable_cycles >= self._stability_threshold:
            new_interval = min(
                self._max_interval,
                self._current_interval * 1.5,  # Increase by 50%
            )
            if new_interval != self._current_interval:
                _LOGGER.debug(
                    "Adaptive polling: stable state, interval=%.1fs",
                    new_interval,
                )
                self._current_interval = new_interval

        # Reset change counter after adjustment
        self._state_change_counter = 0
