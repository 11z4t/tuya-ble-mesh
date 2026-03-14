"""Command request dataclass for transport layer.

Represents a single mesh command request with correlation tracking,
retry policy, and deadline enforcement.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Literal

from tuya_ble_mesh.exceptions import InvalidRequestError


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy for command requests.

    Attributes:
        max_retries: Maximum number of retry attempts (0 = no retries).
        backoff_base: Base backoff in seconds (doubles each retry).
        backoff_max: Maximum backoff cap in seconds.
        jitter: Jitter factor (0.0 = no jitter, 1.0 = full jitter).
    """

    max_retries: int = 3
    backoff_base: float = 0.5
    backoff_max: float = 10.0
    jitter: float = 0.1

    def __post_init__(self) -> None:
        """Validate retry policy parameters."""
        if self.max_retries < 0:
            raise InvalidRequestError("max_retries must be >= 0")
        if self.backoff_base <= 0:
            raise InvalidRequestError("backoff_base must be > 0")
        if self.backoff_max < self.backoff_base:
            raise InvalidRequestError("backoff_max must be >= backoff_base")
        if not 0 <= self.jitter <= 1:
            raise InvalidRequestError("jitter must be in [0, 1]")


@dataclass(frozen=True)
class CommandRequest:
    """A command request in the transport layer.

    Uniquely identified by request_id. Tracks protocol, target, opcode,
    parameters, response expectations, retry policy, deadline, and context.

    Attributes:
        request_id: Unique request identifier (UUID4).
        protocol: Protocol type ('telink' or 'sig').
        target_node: Target mesh address (0xFFFF = broadcast).
        opcode: Protocol-specific opcode.
        params: Command parameters (binary payload).
        expected_response_opcode: Expected response opcode (None = fire-and-forget).
        ttl: Time-to-live in seconds (from creation).
        retry_policy: Retry behavior configuration.
        deadline: Absolute monotonic time when request expires.
        context: Human-readable context (e.g., 'light.turn_on').
        priority: Priority level (0=critical, 1=normal, 2=background).
        created_at: Monotonic time when request was created.
    """

    request_id: uuid.UUID = field(default_factory=uuid.uuid4)
    protocol: Literal["telink", "sig"] = "telink"
    target_node: int = 0
    opcode: int = 0
    params: bytes = b""
    expected_response_opcode: int | None = None
    ttl: float = 60.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    deadline: float = field(init=False)
    context: str = ""
    priority: int = 1
    created_at: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        """Calculate deadline from created_at + ttl."""
        # Frozen dataclass workaround: use object.__setattr__
        object.__setattr__(self, "deadline", self.created_at + self.ttl)

        # Validate parameters
        if not 0 <= self.target_node <= 0xFFFF:
            raise InvalidRequestError(f"target_node must be 0..0xFFFF, got {self.target_node}")
        if not 0 <= self.opcode <= 0xFFFF:
            raise InvalidRequestError(f"opcode must be 0..0xFFFF, got {self.opcode}")
        if self.ttl <= 0:
            raise InvalidRequestError(f"ttl must be > 0, got {self.ttl}")
        if self.priority < 0:
            raise InvalidRequestError(f"priority must be >= 0, got {self.priority}")
        if self.protocol not in ("telink", "sig"):
            raise InvalidRequestError(f"protocol must be 'telink' or 'sig', got {self.protocol}")

    def is_expired(self) -> bool:
        """Return True if request has exceeded its deadline."""
        return time.monotonic() >= self.deadline

    def age(self) -> float:
        """Return age of request in seconds."""
        return time.monotonic() - self.created_at
