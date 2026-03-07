"""Structured logging context for Tuya BLE Mesh operations.

Provides correlation IDs and per-operation context (device MAC, operation
name) via Python's ``contextvars`` module.  Every mesh operation sets a
context so all log records emitted during that operation carry the same
correlation ID, making it possible to trace a single command through the
log stream even when multiple devices operate concurrently.

Usage::

    async with mesh_operation(device_mac, "send_power"):
        await device.send_power(True)

Log output includes::

    [corr=a3f1 mac=DC:23:4F:10:52:C4 op=send_power] GenericOnOff ON sent

"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import random
import string
from collections.abc import AsyncGenerator, Generator
from typing import Any

# --- Context variables ---

#: Short random correlation ID for the current async operation.
_CORR_ID: contextvars.ContextVar[str] = contextvars.ContextVar("corr_id", default="")

#: BLE MAC address being operated on.
_DEVICE_MAC: contextvars.ContextVar[str] = contextvars.ContextVar("device_mac", default="")

#: Human-readable operation name (e.g. "send_power", "connect").
_OPERATION: contextvars.ContextVar[str] = contextvars.ContextVar("operation", default="")


def _new_corr_id() -> str:
    """Generate a 4-character alphanumeric correlation ID.

    Returns:
        Short ID (e.g. ``"a3f1"``) unique within a session.
    """
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=4))  # nosec B311


def get_log_extra() -> dict[str, str]:
    """Return the current context as a dict for ``logging.extra``.

    Returns:
        Dict with keys ``corr``, ``mac``, ``op`` (empty strings when unset).
    """
    return {
        "corr": _CORR_ID.get(),
        "mac": _DEVICE_MAC.get(),
        "op": _OPERATION.get(),
    }


def set_context(mac: str, operation: str, corr_id: str | None = None) -> tuple[Any, Any, Any]:
    """Set the logging context variables and return reset tokens.

    Args:
        mac: BLE MAC address (e.g. ``"DC:23:4F:10:52:C4"``).
        operation: Operation name (e.g. ``"send_power"``).
        corr_id: Correlation ID to reuse (generates new one if None).

    Returns:
        Tuple of ``contextvars.Token`` objects for resetting.
    """
    cid = corr_id if corr_id else _new_corr_id()
    tok_corr = _CORR_ID.set(cid)
    tok_mac = _DEVICE_MAC.set(mac)
    tok_op = _OPERATION.set(operation)
    return tok_corr, tok_mac, tok_op


def reset_context(tokens: tuple[Any, Any, Any]) -> None:
    """Reset context variables to their previous values.

    Args:
        tokens: Tuple returned by :func:`set_context`.
    """
    tok_corr, tok_mac, tok_op = tokens
    _CORR_ID.reset(tok_corr)
    _DEVICE_MAC.reset(tok_mac)
    _OPERATION.reset(tok_op)


@contextlib.contextmanager
def mesh_operation_sync(mac: str, operation: str) -> Generator[str, None, None]:
    """Synchronous context manager to set logging context.

    Args:
        mac: BLE MAC address.
        operation: Operation name.

    Yields:
        Correlation ID for this operation.
    """
    tokens = set_context(mac, operation)
    corr_id = _CORR_ID.get()
    try:
        yield corr_id
    finally:
        reset_context(tokens)


@contextlib.asynccontextmanager
async def mesh_operation(mac: str, operation: str) -> AsyncGenerator[str, None]:
    """Async context manager to set logging context for a mesh operation.

    All log records emitted within this context carry a shared correlation
    ID so that concurrent device operations remain traceable.

    Args:
        mac: BLE MAC address (e.g. ``"DC:23:4F:10:52:C4"``).
        operation: Human-readable operation name (e.g. ``"send_power"``).

    Yields:
        Correlation ID string for this operation.

    Example::

        async with mesh_operation(self._address, "send_power") as corr:
            _LOGGER.info("Sending ON command [corr=%s]", corr)
            await self._client.write_gatt_char(...)
    """
    tokens = set_context(mac, operation)
    corr_id = _CORR_ID.get()
    try:
        yield corr_id
    finally:
        reset_context(tokens)


class MeshLogAdapter(logging.LoggerAdapter):
    """LoggerAdapter that automatically injects mesh context into records.

    Wraps any logger and prepends ``[corr=XXXX mac=... op=...]`` to the
    message string when context variables are set.

    Usage::

        _LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})
        async with mesh_operation(mac, "connect"):
            _LOGGER.info("Attempting BLE connection")
            # Logged as: [corr=a3f1 mac=DC:... op=connect] Attempting BLE connection
    """

    def process(
        self, msg: str, kwargs: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Prepend context prefix to log message.

        Args:
            msg: Original log message.
            kwargs: Logger keyword arguments.

        Returns:
            Tuple of (modified message, kwargs).
        """
        corr = _CORR_ID.get()
        mac = _DEVICE_MAC.get()
        op = _OPERATION.get()
        if corr or mac or op:
            prefix = f"[corr={corr} mac={mac} op={op}] "
            msg = prefix + msg
        return msg, kwargs
