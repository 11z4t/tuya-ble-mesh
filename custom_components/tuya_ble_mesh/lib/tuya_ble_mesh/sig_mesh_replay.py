"""Receive-side replay protection for a shared SIG Mesh network.

Once nodes relay for each other, the GATT proxy forwards the same network
PDU more than once (one copy per relay path). Mesh Profile 3.8.8 requires a
replay protection list: per source address, the last accepted (IV index,
SEQ); anything not strictly newer is dropped.

Pure logic — no I/O, no crypto.
"""

from __future__ import annotations

from collections import OrderedDict

# Upper bound on tracked sources (Tuya bulbs report CRPL=384)
DEFAULT_MAX_SOURCES = 512


class ReplayCache:
    """Per-source (IV index, SEQ) high-water marks."""

    def __init__(self, max_sources: int = DEFAULT_MAX_SOURCES) -> None:
        """Initialize an empty cache.

        Args:
            max_sources: Maximum number of source addresses tracked; the
                least recently seen source is evicted beyond this.
        """
        self._max = max_sources
        self._last: OrderedDict[int, tuple[int, int]] = OrderedDict()

    def __len__(self) -> int:
        """Return the number of tracked sources."""
        return len(self._last)

    def check_and_record(self, src: int, seq: int, iv_index: int) -> bool:
        """Accept a PDU if it is newer than anything seen from ``src``.

        Args:
            src: Source unicast address.
            seq: 24-bit network SEQ.
            iv_index: IV index the PDU was decrypted with.

        Returns:
            True if the PDU is new (and is now recorded), False if it is a
            replay or a relayed duplicate.
        """
        last = self._last.get(src)
        if last is not None and (iv_index, seq) <= last:
            return False
        self._last[src] = (iv_index, seq)
        self._last.move_to_end(src)
        while len(self._last) > self._max:
            self._last.popitem(last=False)
        return True

    def forget(self, src_start: int, count: int = 1) -> None:
        """Drop state for an address range (e.g. a node that was reset).

        Args:
            src_start: First unicast address.
            count: Number of consecutive addresses.
        """
        for addr in range(src_start, src_start + count):
            self._last.pop(addr, None)
