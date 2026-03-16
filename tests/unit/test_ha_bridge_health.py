"""Unit tests for coordinator bridge health, storm detection, and retry logic.

MESH-17: Coverage hardening — reconnect storm, send_command_with_retry retry
paths, and clear-repair-on-recovery.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add project root and lib for imports
_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    _STORM_WINDOW_SECONDS,
    TuyaBLEMeshCoordinator,
)


def _make_device(address: str = "AA:BB:CC:DD:EE:FF") -> MagicMock:
    device = MagicMock()
    device.address = address
    device.firmware_version = None
    return device


@pytest.mark.requires_ha
class TestCheckReconnectStorm:
    """MESH-17: _check_reconnect_storm prunes stale entries and detects loops."""

    def test_no_reconnects_no_storm(self) -> None:
        """Empty timeline — no storm."""
        c = TuyaBLEMeshCoordinator(_make_device())
        assert c._check_reconnect_storm() is False

    def test_few_reconnects_no_storm(self) -> None:
        """Below threshold — no storm."""
        c = TuyaBLEMeshCoordinator(_make_device())
        c._storm_threshold = 5
        now = time.time()
        # Add 3 events within the window
        for _ in range(3):
            c._stats.reconnect_times.append(now)
        assert c._check_reconnect_storm() is False

    def test_storm_detected_at_threshold(self) -> None:
        """Exactly at threshold — storm detected."""
        c = TuyaBLEMeshCoordinator(_make_device())
        c._storm_threshold = 3
        now = time.time()
        for _ in range(3):
            c._stats.reconnect_times.append(now)
        assert c._check_reconnect_storm() is True

    def test_old_events_pruned(self) -> None:
        """Events older than window are pruned — no false storm."""
        c = TuyaBLEMeshCoordinator(_make_device())
        c._storm_threshold = 3
        # Add events far in the past
        old_time = time.time() - _STORM_WINDOW_SECONDS - 60
        for _ in range(10):
            c._stats.reconnect_times.append(old_time)
        assert c._check_reconnect_storm() is False
        # Deque should have been pruned
        assert len(c._stats.reconnect_times) == 0


@pytest.mark.requires_ha
class TestSendCommandRetry:
    """MESH-17: send_command_with_retry handles transient failures and exhaustion."""

    @pytest.mark.asyncio
    async def test_succeeds_on_first_attempt(self) -> None:
        """No retries when command succeeds immediately."""
        c = TuyaBLEMeshCoordinator(_make_device())
        called = []

        async def ok_cmd() -> None:
            called.append(1)

        await c.send_command_with_retry(ok_cmd, max_retries=3, base_delay=0.0)
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self) -> None:
        """Command retries up to max_retries on exception."""
        c = TuyaBLEMeshCoordinator(_make_device())
        attempt_counts = [0]

        async def flaky_cmd() -> None:
            attempt_counts[0] += 1
            if attempt_counts[0] < 3:
                raise ConnectionError("transient")

        with patch(
            "custom_components.tuya_ble_mesh.coordinator.asyncio.sleep",
            new_callable=AsyncMock,
        ):
            await c.send_command_with_retry(flaky_cmd, max_retries=3, base_delay=0.001)

        assert attempt_counts[0] == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries_exhausted(self) -> None:
        """Raises the last exception after all retries are exhausted."""
        c = TuyaBLEMeshCoordinator(_make_device())

        async def always_fails() -> None:
            raise TimeoutError("stuck")

        with patch(
            "custom_components.tuya_ble_mesh.coordinator.asyncio.sleep",
            new_callable=AsyncMock,
        ), pytest.raises(TimeoutError, match="stuck"):
            await c.send_command_with_retry(always_fails, max_retries=2, base_delay=0.001)

    @pytest.mark.asyncio
    async def test_command_errors_incremented_on_failure(self) -> None:
        """Each failed attempt increments stats.command_errors."""
        c = TuyaBLEMeshCoordinator(_make_device())

        async def always_fails() -> None:
            raise OSError("boom")

        with patch(
            "custom_components.tuya_ble_mesh.coordinator.asyncio.sleep",
            new_callable=AsyncMock,
        ), pytest.raises(OSError):
            await c.send_command_with_retry(always_fails, max_retries=2, base_delay=0.001)

        assert c._stats.command_errors == 2
        assert c._stats.total_errors == 2

    @pytest.mark.asyncio
    async def test_concurrent_commands_limited_by_semaphore(self) -> None:
        """Semaphore prevents more than _COMMAND_CONCURRENCY_LIMIT concurrent commands."""
        import asyncio

        from custom_components.tuya_ble_mesh.coordinator import _COMMAND_CONCURRENCY_LIMIT

        c = TuyaBLEMeshCoordinator(_make_device())
        active_concurrent = [0]
        max_concurrent = [0]

        async def slow_cmd() -> None:
            active_concurrent[0] += 1
            max_concurrent[0] = max(max_concurrent[0], active_concurrent[0])
            await asyncio.sleep(0.01)
            active_concurrent[0] -= 1

        tasks = [
            asyncio.create_task(
                c.send_command_with_retry(slow_cmd, max_retries=1, base_delay=0.0)
            )
            for _ in range(_COMMAND_CONCURRENCY_LIMIT + 3)
        ]
        await asyncio.gather(*tasks)
        assert max_concurrent[0] <= _COMMAND_CONCURRENCY_LIMIT


@pytest.mark.requires_ha
class TestClearRepairIssuesOnRecovery:
    """MESH-17: _clear_repair_issues_on_recovery requires hass+entry_id."""

    def test_no_op_without_hass(self) -> None:
        """Does nothing when hass is None."""
        c = TuyaBLEMeshCoordinator(_make_device())
        c._raised_repair_issues.add("bridge_unreachable")
        c._clear_repair_issues_on_recovery()
        # Should not raise; issues not cleared (no hass to call on)
        # But the _raised_repair_issues set is always cleared regardless
        # In actual implementation it returns early if hass is None

    def test_clears_raised_issues_set_with_hass(self) -> None:
        """With hass+entry_id, clears _raised_repair_issues set."""
        from homeassistant.helpers import issue_registry as ir

        c = TuyaBLEMeshCoordinator(_make_device())
        c._hass = MagicMock()
        # Set entry_id on connection_manager (where it's actually used)
        c._conn_mgr._hass = MagicMock()
        c._conn_mgr._entry_id = "entry_xyz"
        c._raised_repair_issues.add("bridge_unreachable")

        with patch.object(ir, "async_delete_issue"):
            c._clear_repair_issues_on_recovery()

        assert len(c._raised_repair_issues) == 0
