"""Unit tests for transport/request.py — RetryPolicy and CommandRequest validation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.exceptions import InvalidRequestError  # noqa: E402
from tuya_ble_mesh.transport.request import CommandRequest, RetryPolicy  # noqa: E402


class TestRetryPolicyValidation:
    """Test RetryPolicy __post_init__ validation."""

    def test_negative_max_retries_raises(self) -> None:
        with pytest.raises(InvalidRequestError, match="max_retries must be >= 0"):
            RetryPolicy(max_retries=-1)

    def test_non_positive_backoff_base_raises(self) -> None:
        with pytest.raises(InvalidRequestError, match="backoff_base must be > 0"):
            RetryPolicy(backoff_base=0.0)

    def test_backoff_max_less_than_base_raises(self) -> None:
        with pytest.raises(InvalidRequestError, match="backoff_max must be >= backoff_base"):
            RetryPolicy(backoff_base=5.0, backoff_max=1.0)

    def test_jitter_below_zero_raises(self) -> None:
        with pytest.raises(InvalidRequestError, match="jitter must be in"):
            RetryPolicy(jitter=-0.1)

    def test_jitter_above_one_raises(self) -> None:
        with pytest.raises(InvalidRequestError, match="jitter must be in"):
            RetryPolicy(jitter=1.1)

    def test_valid_defaults(self) -> None:
        p = RetryPolicy()
        assert p.max_retries == 3
        assert p.backoff_base == 0.5
        assert p.backoff_max == 10.0
        assert p.jitter == 0.1

    def test_valid_zero_max_retries(self) -> None:
        p = RetryPolicy(max_retries=0)
        assert p.max_retries == 0

    def test_valid_zero_jitter(self) -> None:
        p = RetryPolicy(jitter=0.0)
        assert p.jitter == 0.0

    def test_valid_full_jitter(self) -> None:
        p = RetryPolicy(jitter=1.0)
        assert p.jitter == 1.0


class TestCommandRequestValidation:
    """Test CommandRequest __post_init__ validation."""

    def test_target_node_too_large_raises(self) -> None:
        with pytest.raises(InvalidRequestError, match=r"target_node must be 0\.\.0xFFFF"):
            CommandRequest(target_node=0x10000)

    def test_target_node_negative_raises(self) -> None:
        with pytest.raises(InvalidRequestError, match=r"target_node must be 0\.\.0xFFFF"):
            CommandRequest(target_node=-1)

    def test_opcode_too_large_raises(self) -> None:
        with pytest.raises(InvalidRequestError, match=r"opcode must be 0\.\.0xFFFF"):
            CommandRequest(opcode=0x10000)

    def test_opcode_negative_raises(self) -> None:
        with pytest.raises(InvalidRequestError, match=r"opcode must be 0\.\.0xFFFF"):
            CommandRequest(opcode=-1)

    def test_non_positive_ttl_raises(self) -> None:
        with pytest.raises(InvalidRequestError, match="ttl must be > 0"):
            CommandRequest(ttl=0.0)

    def test_negative_priority_raises(self) -> None:
        with pytest.raises(InvalidRequestError, match="priority must be >= 0"):
            CommandRequest(priority=-1)

    def test_invalid_protocol_raises(self) -> None:
        with pytest.raises(InvalidRequestError, match="protocol must be"):
            CommandRequest(protocol="zigbee")  # type: ignore[arg-type]

    def test_valid_defaults(self) -> None:
        r = CommandRequest()
        assert r.protocol == "telink"
        assert r.target_node == 0
        assert r.opcode == 0
        assert r.ttl == 60.0
        assert r.priority == 1

    def test_valid_sig_protocol(self) -> None:
        r = CommandRequest(protocol="sig")
        assert r.protocol == "sig"

    def test_deadline_set_from_created_at_plus_ttl(self) -> None:
        r = CommandRequest(ttl=30.0)
        assert abs(r.deadline - r.created_at - 30.0) < 0.01

    def test_broadcast_address_allowed(self) -> None:
        r = CommandRequest(target_node=0xFFFF)
        assert r.target_node == 0xFFFF
