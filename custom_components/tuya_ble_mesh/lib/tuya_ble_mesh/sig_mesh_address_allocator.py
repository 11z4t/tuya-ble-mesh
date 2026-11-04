"""Unicast address allocation for a shared SIG Mesh network.

Every node occupies ``element_count`` consecutive unicast addresses starting
at its primary address. The allocator is stateless: callers pass the ranges
in use (derived from stored node data) each time, so nothing can drift.

Pure logic — no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable

from tuya_ble_mesh.exceptions import ProtocolError

UNICAST_MAX = 0x7FFF
# 0x0001 is the provisioner (Home Assistant)
DEFAULT_FIRST_NODE_ADDR = 0x0002


class AddressSpaceExhaustedError(ProtocolError):  # type: ignore[misc]
    """No free unicast range is large enough for the requested element count."""


def allocate_unicast(
    element_count: int,
    used: Iterable[tuple[int, int]],
    *,
    first: int = DEFAULT_FIRST_NODE_ADDR,
) -> int:
    """Return the lowest free primary address that fits ``element_count``.

    Args:
        element_count: Number of elements the new node has (>= 1).
        used: ``(start, count)`` ranges already taken (nodes, the
            provisioner, reserved/quarantined ranges).
        first: Lowest address that may be allocated.

    Returns:
        Primary unicast address for the new node.

    Raises:
        ProtocolError: If ``element_count`` is out of range.
        AddressSpaceExhaustedError: If no gap is large enough.
    """
    if not 1 <= element_count <= 0xFF:
        msg = f"element_count must be 1..255, got {element_count}"
        raise ProtocolError(msg)
    ranges = sorted((start, start + count) for start, count in used if count > 0)
    candidate = first
    for start, end in ranges:
        if end <= candidate:
            continue
        if start >= candidate + element_count:
            break
        candidate = max(candidate, end)
    if candidate + element_count - 1 > UNICAST_MAX:
        msg = f"No free unicast range for {element_count} element(s)"
        raise AddressSpaceExhaustedError(msg)
    return candidate


def range_contains(start: int, count: int, addr: int) -> bool:
    """Return True if ``addr`` lies in ``[start, start + count)``."""
    return start <= addr < start + count
