"""Tests for transport correlation engine."""

import uuid

import pytest
from tuya_ble_mesh.exceptions import CorrelationConflictError
from tuya_ble_mesh.transport import CommandRequest, CorrelationEngine


def test_correlation_register_and_match():
    """Test registering a request and matching a response."""
    engine = CorrelationEngine()
    request = CommandRequest(
        protocol="telink",
        target_node=0x0001,
        opcode=0xD0,
        expected_response_opcode=0xDC,
    )

    # Register
    key = engine.register(request, sequence=100)
    assert engine.pending_count() == 1
    assert key.opcode == 0xDC
    assert key.destination == 0x0001
    assert key.sequence == 100

    # Match response
    matched = engine.match_response(opcode=0xDC, source=0x0001, sequence=100)
    assert matched is not None
    assert matched.request_id == request.request_id
    assert engine.pending_count() == 0


def test_correlation_expire_stale():
    """Test expiring stale requests that exceed deadline."""
    engine = CorrelationEngine()
    request = CommandRequest(
        protocol="telink",
        target_node=0x0001,
        opcode=0xD0,
        ttl=0.001,  # 1ms TTL — will expire immediately
    )

    engine.register(request, sequence=100)
    assert engine.pending_count() == 1

    # Wait for expiration
    import time

    time.sleep(0.01)

    expired = engine.expire_stale()
    assert len(expired) == 1
    assert expired[0].request_id == request.request_id
    assert engine.pending_count() == 0


def test_correlation_no_opcode_collision():
    """Test that same opcode but different target does not collide."""
    engine = CorrelationEngine()

    request1 = CommandRequest(
        protocol="telink",
        target_node=0x0001,
        opcode=0xD0,
        expected_response_opcode=0xDC,
    )
    request2 = CommandRequest(
        protocol="telink",
        target_node=0x0002,  # Different target
        opcode=0xD0,
        expected_response_opcode=0xDC,
    )

    engine.register(request1, sequence=100)
    engine.register(request2, sequence=101)
    assert engine.pending_count() == 2

    # Match response for target 0x0001
    matched1 = engine.match_response(opcode=0xDC, source=0x0001, sequence=100)
    assert matched1 is not None
    assert matched1.request_id == request1.request_id

    # Target 0x0002 should still be pending
    assert engine.pending_count() == 1

    # Match response for target 0x0002
    matched2 = engine.match_response(opcode=0xDC, source=0x0002, sequence=101)
    assert matched2 is not None
    assert matched2.request_id == request2.request_id
    assert engine.pending_count() == 0


def test_correlation_cancel():
    """Test cancelling a pending request by ID."""
    engine = CorrelationEngine()
    request = CommandRequest(
        protocol="telink",
        target_node=0x0001,
        opcode=0xD0,
    )

    engine.register(request, sequence=100)
    assert engine.pending_count() == 1

    # Cancel
    cancelled = engine.cancel(request.request_id)
    assert cancelled is True
    assert engine.pending_count() == 0

    # Cancel again should fail
    cancelled = engine.cancel(request.request_id)
    assert cancelled is False


def test_correlation_register_duplicate_fails():
    """Test that registering the same request_id twice fails."""
    engine = CorrelationEngine()
    request_id = uuid.uuid4()
    request1 = CommandRequest(
        request_id=request_id,
        protocol="telink",
        target_node=0x0001,
        opcode=0xD0,
    )
    request2 = CommandRequest(
        request_id=request_id,  # Same ID
        protocol="telink",
        target_node=0x0002,
        opcode=0xD1,
    )

    engine.register(request1, sequence=100)

    with pytest.raises(CorrelationConflictError, match="already registered"):
        engine.register(request2, sequence=101)


def test_correlation_no_match_returns_none():
    """Test that mismatched response returns None."""
    engine = CorrelationEngine()
    request = CommandRequest(
        protocol="telink",
        target_node=0x0001,
        opcode=0xD0,
        expected_response_opcode=0xDC,
    )

    engine.register(request, sequence=100)

    # Wrong opcode
    matched = engine.match_response(opcode=0xDD, source=0x0001, sequence=100)
    assert matched is None

    # Wrong source
    matched = engine.match_response(opcode=0xDC, source=0x0002, sequence=100)
    assert matched is None

    # Wrong sequence
    matched = engine.match_response(opcode=0xDC, source=0x0001, sequence=101)
    assert matched is None

    # Request should still be pending
    assert engine.pending_count() == 1
