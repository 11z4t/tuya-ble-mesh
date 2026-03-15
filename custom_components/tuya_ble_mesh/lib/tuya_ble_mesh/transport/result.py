"""Command result dataclass for transport layer.

Represents the outcome of a command request with status, response data,
latency, retry count, and error information.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from tuya_ble_mesh.exceptions import InvalidResultError

# Error messages
_ERR_MISSING_ERROR = "status='error' requires error to be set"
_ERR_UNEXPECTED_ERROR = "status='success' should not have error set"
_ERR_NEGATIVE_LATENCY = "latency_ms must be >= 0, got {}"
_ERR_NEGATIVE_RETRIES = "retries_used must be >= 0, got {}"


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
            raise InvalidResultError(_ERR_MISSING_ERROR)
        if self.status == "success" and self.error is not None:
            raise InvalidResultError(_ERR_UNEXPECTED_ERROR)
        if self.latency_ms < 0:
            raise InvalidResultError(_ERR_NEGATIVE_LATENCY.format(self.latency_ms))
        if self.retries_used < 0:
            raise InvalidResultError(_ERR_NEGATIVE_RETRIES.format(self.retries_used))

    def is_successful(self) -> bool:
        """Return True if command succeeded."""
        return self.status == "success"

    def is_failure(self) -> bool:
        """Return True if command failed (error or timeout)."""
        return self.status in ("error", "timeout")
