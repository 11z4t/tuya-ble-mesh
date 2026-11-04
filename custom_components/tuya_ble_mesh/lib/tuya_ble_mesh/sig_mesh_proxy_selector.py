"""Rank candidate GATT proxy nodes for a shared SIG Mesh network.

Any node with the GATT Proxy feature can carry the whole network. Pick the
best-heard one, avoid nodes that just failed, and prefer the current proxy
so the link doesn't flap between similar candidates.

Pure logic — no I/O.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

# A failed connect costs this many dB of ranking, decaying linearly to zero
FAILURE_PENALTY_DB = 20.0
FAILURE_PENALTY_DECAY_S = 300.0
# The current/last proxy wins ties up to this many dB
STICKINESS_DB = 6.0
# Candidates not heard for longer than this are skipped
STALE_AFTER_S = 120.0


@dataclass(frozen=True)
class ProxyCandidate:
    """A node currently advertising that we might connect to."""

    address: str
    rssi: int | None
    last_seen: float  # time.monotonic() of the last advertisement


@dataclass
class ProxySelector:
    """Stateful ranking with failure penalties and stickiness."""

    preferred: frozenset[str] = frozenset()
    excluded: frozenset[str] = frozenset()
    _failures: dict[str, list[float]] = field(default_factory=dict)
    _last_proxy: str | None = None

    def record_failure(self, address: str, now: float | None = None) -> None:
        """Penalise ``address`` after a failed connect or a link drop."""
        stamp = time.monotonic() if now is None else now
        self._failures.setdefault(address.upper(), []).append(stamp)

    def record_success(self, address: str) -> None:
        """Remember ``address`` as the current proxy."""
        self._last_proxy = address.upper()

    @property
    def last_proxy(self) -> str | None:
        """Return the most recent successful proxy address."""
        return self._last_proxy

    def _penalty(self, address: str, now: float) -> float:
        stamps = self._failures.get(address, [])
        live = [t for t in stamps if now - t < FAILURE_PENALTY_DECAY_S]
        if live:
            self._failures[address] = live
        else:
            self._failures.pop(address, None)
        return sum(FAILURE_PENALTY_DB * (1 - (now - t) / FAILURE_PENALTY_DECAY_S) for t in live)

    def rank(self, candidates: list[ProxyCandidate], now: float | None = None) -> list[str]:
        """Return candidate addresses best first.

        Args:
            candidates: Nodes currently heard.
            now: ``time.monotonic()`` override (tests).

        Returns:
            Addresses ordered by score (excluded and stale ones dropped).
        """
        ts = time.monotonic() if now is None else now
        scored: list[tuple[float, str]] = []
        for cand in candidates:
            addr = cand.address.upper()
            if addr in self.excluded or ts - cand.last_seen > STALE_AFTER_S:
                continue
            score = float(cand.rssi if cand.rssi is not None else -100)
            score -= self._penalty(addr, ts)
            if addr == self._last_proxy:
                score += STICKINESS_DB
            if addr in self.preferred:
                score += 100.0
            scored.append((score, addr))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [addr for _, addr in scored]
