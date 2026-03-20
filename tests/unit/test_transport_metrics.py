"""Unit tests for transport/metrics.py — TransportMetrics."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.transport.metrics import TransportMetrics  # noqa: E402
from tuya_ble_mesh.transport.result import CommandResult  # noqa: E402


def _result(
    status: str = "success",
    latency_ms: float = 0.0,
    retries_used: int = 0,
) -> CommandResult:
    kwargs: dict = {"request_id": uuid.uuid4(), "status": status, "latency_ms": latency_ms}
    if status == "error":
        kwargs["error"] = RuntimeError("fail")
    kwargs["retries_used"] = retries_used
    return CommandResult(**kwargs)


class TestRecordResult:
    """Test record_result() counter increments."""

    def test_success_increments_counters(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("success"), opcode=0x01)
        assert m.commands_sent == 1
        assert m.commands_succeeded == 1
        assert m.commands_failed == 0

    def test_timeout_increments_timed_out(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("timeout"), opcode=0x01)
        assert m.commands_timed_out == 1
        assert m.commands_succeeded == 0

    def test_error_increments_failed(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("error"), opcode=0x01)
        assert m.commands_failed == 1

    def test_cancelled_increments_cancelled(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("cancelled"), opcode=0x02)
        assert m.commands_cancelled == 1

    def test_coalesced_increments_coalesced(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("coalesced"), opcode=0x02)
        assert m.commands_coalesced == 1

    def test_retries_accumulated(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("success", retries_used=2), opcode=0x01)
        m.record_result(_result("success", retries_used=1), opcode=0x01)
        assert m.retries_total == 3

    def test_latency_recorded(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("success", latency_ms=42.0), opcode=0x01)
        assert len(m._latency_samples) == 1
        assert m._latency_samples[0] == 42.0

    def test_zero_latency_not_recorded(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("success", latency_ms=0.0), opcode=0x01)
        assert len(m._latency_samples) == 0


class TestLatencyPercentiles:
    """Test p50, p95, p99 percentile calculations."""

    def test_p50_empty_is_zero(self) -> None:
        assert TransportMetrics().p50 == 0.0

    def test_p95_empty_is_zero(self) -> None:
        assert TransportMetrics().p95 == 0.0

    def test_p99_empty_is_zero(self) -> None:
        assert TransportMetrics().p99 == 0.0

    def test_p50_single_sample(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("success", latency_ms=100.0), opcode=0x01)
        assert m.p50 == pytest.approx(100.0)

    def test_p95_with_samples(self) -> None:
        m = TransportMetrics()
        for i in range(1, 21):  # 20 samples: 1..20
            m.record_result(_result("success", latency_ms=float(i)), opcode=0x01)
        # p95 of [1..20]: k = 19 * 0.95 = 18.05 → f=18, c=19
        # sorted_samples[18] + 0.05 * (sorted_samples[19] - sorted_samples[18])
        # = 19.0 + 0.05 * 1.0 = 19.05
        assert m.p95 == pytest.approx(19.05)

    def test_p99_with_samples(self) -> None:
        m = TransportMetrics()
        for i in range(1, 101):  # 100 samples: 1..100
            m.record_result(_result("success", latency_ms=float(i)), opcode=0x01)
        # p99 of [1..100]: k = 99 * 0.99 = 98.01 → f=98, c=99
        # sorted[98] + 0.01 * (sorted[99] - sorted[98]) = 99.0 + 0.01 = 99.01
        assert m.p99 == pytest.approx(99.01)


class TestSuccessRate:
    """Test success_rate()."""

    def test_success_rate_zero_commands_is_zero(self) -> None:
        assert TransportMetrics().success_rate() == 0.0

    def test_success_rate_all_success(self) -> None:
        m = TransportMetrics()
        for _ in range(5):
            m.record_result(_result("success"), opcode=0x01)
        assert m.success_rate() == pytest.approx(1.0)

    def test_success_rate_partial(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("success"), opcode=0x01)
        m.record_result(_result("timeout"), opcode=0x01)
        m.record_result(_result("timeout"), opcode=0x01)
        m.record_result(_result("timeout"), opcode=0x01)
        assert m.success_rate() == pytest.approx(0.25)


class TestOpcodeStats:
    """Test per-opcode statistics."""

    def test_opcode_stats_default_zero(self) -> None:
        m = TransportMetrics()
        stats = m.opcode_stats(0xFF)
        assert stats.sent == 0
        assert stats.succeeded == 0

    def test_opcode_stats_after_record(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("success", latency_ms=10.0), opcode=0x04)
        m.record_result(_result("timeout"), opcode=0x04)
        stats = m.opcode_stats(0x04)
        assert stats.sent == 2
        assert stats.succeeded == 1
        assert stats.timed_out == 1

    def test_all_opcode_stats_returns_dict(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("success"), opcode=0x01)
        m.record_result(_result("error"), opcode=0x02)
        all_stats = m.all_opcode_stats()
        assert 0x01 in all_stats
        assert 0x02 in all_stats


class TestReset:
    """Test reset() clears all counters."""

    def test_reset_zeroes_all_counters(self) -> None:
        m = TransportMetrics()
        m.record_result(_result("success", latency_ms=10.0, retries_used=2), opcode=0x01)
        m.record_result(_result("cancelled"), opcode=0x02)
        m.record_in_flight(5)

        m.reset()

        assert m.commands_sent == 0
        assert m.commands_succeeded == 0
        assert m.commands_failed == 0
        assert m.commands_timed_out == 0
        assert m.commands_cancelled == 0
        assert m.commands_coalesced == 0
        assert m.retries_total == 0
        assert m.in_flight_max == 0
        assert len(m._latency_samples) == 0
        assert len(m._per_opcode_stats) == 0
