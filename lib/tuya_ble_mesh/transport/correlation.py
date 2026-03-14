"""Correlation engine for request/response matching.

Tracks in-flight requests and matches incoming responses based on
correlation keys (opcode, destination, sequence, request_id).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import namedtuple
from typing import TYPE_CHECKING

from tuya_ble_mesh.exceptions import CorrelationConflictError

if TYPE_CHECKING:
    from tuya_ble_mesh.transport.request import CommandRequest

_LOGGER = logging.getLogger(__name__)

# Correlation key uniquely identifies a pending request
CorrelationKey = namedtuple("CorrelationKey", ["opcode", "destination", "sequence", "request_id"])


class CorrelationEngine:
    """Matches responses to pending requests using correlation keys.

    Tracks pending requests and provides efficient lookup when responses arrive.
    Periodically expires stale requests that exceed their deadlines.
    """

    def __init__(self) -> None:
        """Initialize an empty correlation engine."""
        self._pending: dict[CorrelationKey, CommandRequest] = {}
        self._by_request_id: dict[uuid.UUID, CorrelationKey] = {}

    def register(
        self,
        request: CommandRequest,
        sequence: int,
    ) -> CorrelationKey:
        """Register a pending request with its sequence number.

        Args:
            request: Command request to track.
            sequence: Protocol sequence number used in transmission.

        Returns:
            Correlation key for this request.

        Raises:
            CorrelationConflictError: If request_id is already registered.
        """
        if request.request_id in self._by_request_id:
            msg = f"Request {request.request_id} already registered"
            raise CorrelationConflictError(msg)

        key = CorrelationKey(
            opcode=request.expected_response_opcode or request.opcode,
            destination=request.target_node,
            sequence=sequence,
            request_id=request.request_id,
        )

        self._pending[key] = request
        self._by_request_id[request.request_id] = key

        _LOGGER.debug(
            "Registered request %s: opcode=0x%04X dest=0x%04X seq=%d",
            request.request_id,
            key.opcode,
            key.destination,
            key.sequence,
        )

        return key

    def match_response(
        self,
        opcode: int,
        source: int,
        sequence: int,
    ) -> CommandRequest | None:
        """Match an incoming response to a pending request.

        Args:
            opcode: Response opcode.
            source: Source mesh address (should match request destination).
            sequence: Protocol sequence number.

        Returns:
            Matching CommandRequest if found, None otherwise.
        """
        # Try to find a matching key
        # We match on (opcode, destination=source, sequence)
        # request_id can be any, so we need to scan
        for key, request in list(self._pending.items()):
            if key.opcode == opcode and key.destination == source and key.sequence == sequence:
                # Found a match — remove from tracking
                del self._pending[key]
                del self._by_request_id[request.request_id]

                _LOGGER.debug(
                    "Matched response: request=%s opcode=0x%04X source=0x%04X seq=%d",
                    request.request_id,
                    opcode,
                    source,
                    sequence,
                )

                return request

        _LOGGER.debug(
            "No match for response: opcode=0x%04X source=0x%04X seq=%d",
            opcode,
            source,
            sequence,
        )
        return None

    def cancel(self, request_id: uuid.UUID) -> bool:
        """Cancel a pending request by ID.

        Args:
            request_id: Request ID to cancel.

        Returns:
            True if request was found and cancelled, False otherwise.
        """
        key = self._by_request_id.get(request_id)
        if key is None:
            return False

        del self._pending[key]
        del self._by_request_id[request_id]

        _LOGGER.debug("Cancelled request %s", request_id)
        return True

    def expire_stale(self) -> list[CommandRequest]:
        """Remove and return all expired pending requests.

        Returns:
            List of expired CommandRequest objects.
        """
        now = time.monotonic()
        expired: list[CommandRequest] = []

        for key, request in list(self._pending.items()):
            if request.deadline <= now:
                expired.append(request)
                del self._pending[key]
                del self._by_request_id[request.request_id]

        if expired:
            _LOGGER.warning("Expired %d stale request(s)", len(expired))

        return expired

    def pending_count(self) -> int:
        """Return the number of pending requests."""
        return len(self._pending)

    def clear(self) -> None:
        """Clear all pending requests (for shutdown)."""
        count = len(self._pending)
        self._pending.clear()
        self._by_request_id.clear()
        if count > 0:
            _LOGGER.debug("Cleared %d pending request(s)", count)
