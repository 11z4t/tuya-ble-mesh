"""Command result dataclass for transport layer.

Represents the outcome of a command request with status, response data,
latency, retry count, and error information.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CommandResult:
    """The result of a command request.

    Attributes:
        request_id: UUID of the original request.
        status: Outcome status.
        response_data: Response payload (None if no response or error).
        latency_ms: Total latency from enqueue to completion (milliseconds).
        retries_used: Number of retries performed (0 = succeeded on first try).
        error: Exception if status is 'error' (None otherwise).
    """

    request_id: uuid.UUID
    status: Literal["success", "timeout", "error", "cancelled", "coalesced"]
    response_data: bytes | None = None
    latency_ms: float = 0.0
    retries_used: int = 0
    error: Exception | None = None

    def __post_init__(self) -> None:
        """Validate result consistency."""
        if self.status == "error" and self.error is None:
            raise ValueError("status='error' requires error to be set")
        if self.status == "success" and self.error is not None:
            raise ValueError("status='success' should not have error set")
        if self.latency_ms < 0:
            raise ValueError(f"latency_ms must be >= 0, got {self.latency_ms}")
        if self.retries_used < 0:
            raise ValueError(f"retries_used must be >= 0, got {self.retries_used}")

    def is_successful(self) -> bool:
        """Return True if command succeeded."""
        return self.status == "success"

    def is_failure(self) -> bool:
        """Return True if command failed (error or timeout)."""
        return self.status in ("error", "timeout")
