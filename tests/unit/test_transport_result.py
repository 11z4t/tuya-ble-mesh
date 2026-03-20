"""Unit tests for transport/result.py — CommandResult validation and helpers."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.exceptions import InvalidResultError  # noqa: E402
from tuya_ble_mesh.transport.result import CommandResult  # noqa: E402


def _rid() -> uuid.UUID:
    return uuid.uuid4()


class TestCommandResultValidation:
    """Test __post_init__ validation in CommandResult."""

    def test_error_status_requires_error(self) -> None:
        with pytest.raises(InvalidResultError, match="requires error"):
            CommandResult(request_id=_rid(), status="error")

    def test_success_status_must_not_have_error(self) -> None:
        with pytest.raises(InvalidResultError, match="should not have error"):
            CommandResult(
                request_id=_rid(),
                status="success",
                error=ValueError("unexpected"),
            )

    def test_negative_latency_raises(self) -> None:
        with pytest.raises(InvalidResultError, match="latency_ms must be >= 0"):
            CommandResult(request_id=_rid(), status="success", latency_ms=-1.0)

    def test_negative_retries_raises(self) -> None:
        with pytest.raises(InvalidResultError, match="retries_used must be >= 0"):
            CommandResult(request_id=_rid(), status="success", retries_used=-1)

    def test_valid_success_result(self) -> None:
        r = CommandResult(request_id=_rid(), status="success", latency_ms=10.0)
        assert r.status == "success"

    def test_valid_error_result(self) -> None:
        r = CommandResult(request_id=_rid(), status="error", error=RuntimeError("fail"))
        assert r.status == "error"

    def test_valid_timeout_result(self) -> None:
        r = CommandResult(request_id=_rid(), status="timeout")
        assert r.status == "timeout"

    def test_valid_cancelled_result(self) -> None:
        r = CommandResult(request_id=_rid(), status="cancelled")
        assert r.status == "cancelled"

    def test_valid_coalesced_result(self) -> None:
        r = CommandResult(request_id=_rid(), status="coalesced")
        assert r.status == "coalesced"


class TestCommandResultHelpers:
    """Test is_successful() and is_failure() helper methods."""

    def test_is_successful_true_for_success(self) -> None:
        r = CommandResult(request_id=_rid(), status="success")
        assert r.is_successful() is True

    def test_is_successful_false_for_timeout(self) -> None:
        r = CommandResult(request_id=_rid(), status="timeout")
        assert r.is_successful() is False

    def test_is_successful_false_for_error(self) -> None:
        r = CommandResult(request_id=_rid(), status="error", error=RuntimeError("x"))
        assert r.is_successful() is False

    def test_is_failure_true_for_error(self) -> None:
        r = CommandResult(request_id=_rid(), status="error", error=RuntimeError("x"))
        assert r.is_failure() is True

    def test_is_failure_true_for_timeout(self) -> None:
        r = CommandResult(request_id=_rid(), status="timeout")
        assert r.is_failure() is True

    def test_is_failure_false_for_success(self) -> None:
        r = CommandResult(request_id=_rid(), status="success")
        assert r.is_failure() is False

    def test_is_failure_false_for_cancelled(self) -> None:
        r = CommandResult(request_id=_rid(), status="cancelled")
        assert r.is_failure() is False
