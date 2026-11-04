"""Minimal stub for homeassistant.helpers.dispatcher (in-process signals)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_SIGNALS: dict[str, list[Callable[..., Any]]] = {}


def async_dispatcher_connect(
    hass: Any, signal: str, target: Callable[..., Any]
) -> Callable[[], None]:
    _SIGNALS.setdefault(signal, []).append(target)

    def _remove() -> None:
        if target in _SIGNALS.get(signal, []):
            _SIGNALS[signal].remove(target)

    return _remove


def async_dispatcher_send(hass: Any, signal: str, *args: Any) -> None:
    for target in list(_SIGNALS.get(signal, [])):
        target(*args)
