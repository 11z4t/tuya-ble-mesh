"""Transport layer metrics tracking.

Provides lightweight counters, gauges, and latency histograms for
command success rates, retry counts, and performance monitoring.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tuya_ble_mesh.transport.result import CommandResult


@dataclass
class OpcodeStats:
    """Per-opcode statistics.

    Attributes:
        sent: Total commands sent with this opcode.
        succeeded: Total commands that succeeded.
        failed: Total commands that failed (error).
        timed_out: Total commands that timed out.
        latency_samples: Recent latency samples (ms).
    """

    sent: int = 0
    succeeded: int = 0
    failed: int = 0
    timed_out: int = 0
    latency_samples: deque[float] = field(default_factory=lambda: deque(maxlen=100))


class TransportMetrics:
    """Lightweight metrics for transport layer.

    Tracks command counts, latency percentiles, retry statistics,
    and per-opcode breakdown. Does not depend on external libraries
    (no prometheus_client).
    """

    def __init__(self) -> None:
        """Initialize metrics with zero counters."""
        self.commands_sent: int = 0
        self.commands_succeeded: int = 0
        self.commands_failed: int = 0
        self.commands_timed_out: int = 0
        self.commands_cancelled: int = 0
        self.commands_coalesced: int = 0
        self.retries_total: int = 0
        self.in_flight_max: int = 0

        # Latency tracking (last 100 samples)
        self._latency_samples: deque[float] = deque(maxlen=100)

        # Per-opcode breakdown
        self._per_opcode_stats: dict[int, OpcodeStats] = defaultdict(OpcodeStats)

    def record_result(self, result: CommandResult, opcode: int) -> None:
        """Record a command result.

        Args:
            result: Command result to record.
            opcode: Opcode of the command (for per-opcode stats).
        """
        self.commands_sent += 1

        if result.status == "success":
            self.commands_succeeded += 1
            self._per_opcode_stats[opcode].succeeded += 1
        elif result.status == "timeout":
            self.commands_timed_out += 1
            self._per_opcode_stats[opcode].timed_out += 1
        elif result.status == "error":
            self.commands_failed += 1
            self._per_opcode_stats[opcode].failed += 1
        elif result.status == "cancelled":
            self.commands_cancelled += 1
        elif result.status == "coalesced":
            self.commands_coalesced += 1

        self.retries_total += result.retries_used

        if result.latency_ms > 0:
            self._latency_samples.append(result.latency_ms)
            self._per_opcode_stats[opcode].latency_samples.append(result.latency_ms)

        self._per_opcode_stats[opcode].sent += 1

    def record_in_flight(self, count: int) -> None:
        """Record current in-flight command count (for max tracking).

        Args:
            count: Current number of in-flight commands.
        """
        if count > self.in_flight_max:
            self.in_flight_max = count

    @property
    def p50(self) -> float:
        """Return 50th percentile latency (ms)."""
        return self._percentile(self._latency_samples, 0.5)

    @property
    def p95(self) -> float:
        """Return 95th percentile latency (ms)."""
        return self._percentile(self._latency_samples, 0.95)

    @property
    def p99(self) -> float:
        """Return 99th percentile latency (ms)."""
        return self._percentile(self._latency_samples, 0.99)

    @staticmethod
    def _percentile(samples: deque[float], p: float) -> float:
        """Calculate percentile from samples.

        Args:
            samples: Sample values.
            p: Percentile (0.0 to 1.0).

        Returns:
            Percentile value, or 0.0 if no samples.
        """
        if not samples:
            return 0.0
        sorted_samples = sorted(samples)
        n = len(sorted_samples)
        k = (n - 1) * p
        f = int(k)
        c = f + 1 if f < n - 1 else f
        return sorted_samples[f] + (k - f) * (sorted_samples[c] - sorted_samples[f])

    def success_rate(self) -> float:
        """Return overall success rate (0.0 to 1.0)."""
        if self.commands_sent == 0:
            return 0.0
        return self.commands_succeeded / self.commands_sent

    def opcode_stats(self, opcode: int) -> OpcodeStats:
        """Return per-opcode statistics.

        Args:
            opcode: Opcode to query.

        Returns:
            OpcodeStats for the given opcode (default zeros if never seen).
        """
        return self._per_opcode_stats[opcode]

    def all_opcode_stats(self) -> dict[int, OpcodeStats]:
        """Return all per-opcode statistics.

        Returns:
            Dictionary mapping opcode to OpcodeStats.
        """
        return dict(self._per_opcode_stats)

    def reset(self) -> None:
        """Reset all metrics to zero (for testing)."""
        self.commands_sent = 0
        self.commands_succeeded = 0
        self.commands_failed = 0
        self.commands_timed_out = 0
        self.commands_cancelled = 0
        self.commands_coalesced = 0
        self.retries_total = 0
        self.in_flight_max = 0
        self._latency_samples.clear()
        self._per_opcode_stats.clear()
