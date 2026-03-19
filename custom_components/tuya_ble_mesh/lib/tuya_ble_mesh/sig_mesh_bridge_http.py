"""HTTP session management for BLE bridge daemon communication.

Provides a reusable mixin for making HTTP GET/POST requests to the
``ble_mesh_daemon`` API. Used by both SIGMeshBridgeDevice and TelinkBridgeDevice.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from tuya_ble_mesh.const import DEFAULT_HTTP_TIMEOUT
from tuya_ble_mesh.exceptions import MeshConnectionError

# Bridge daemon default config
DEFAULT_BRIDGE_PORT = 8099
COMMAND_TIMEOUT = 60.0
POLL_INTERVAL = 2.0
MAX_POLL_ATTEMPTS = 30

# Retry config for BLE write commands
DEFAULT_MAX_RETRIES = 3
RETRY_INITIAL_BACKOFF = 1.0
RETRY_BACKOFF_MULTIPLIER = 2.0

# Delay between bridge connection retries (seconds)
BRIDGE_CONNECT_RETRY_DELAY = 2.0


class BridgeHTTPMixin:
    """Shared HTTP session management for bridge device classes.

    Subclasses must set ``_bridge_url`` and ``_session`` in ``__init__``.
    """

    _bridge_url: str
    _session: aiohttp.ClientSession | None

    def _get_session(self) -> aiohttp.ClientSession:
        """Get or create a shared aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _close_session(self) -> None:
        """Close the shared aiohttp session."""
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _http_get(self, path: str, timeout: float = DEFAULT_HTTP_TIMEOUT) -> dict[str, Any]:
        """Make an HTTP GET request to the bridge daemon."""
        url = f"{self._bridge_url}{path}"
        try:
            session = self._get_session()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                result: dict[str, Any] = await resp.json()
                return result
        except (TimeoutError, aiohttp.ClientError) as exc:
            msg = f"Bridge HTTP GET {path} failed: {exc}"
            raise MeshConnectionError(msg) from exc

    async def _http_post(
        self,
        path: str,
        data: dict[str, Any],
        timeout: float = DEFAULT_HTTP_TIMEOUT,
    ) -> dict[str, Any]:
        """Make an HTTP POST request to the bridge daemon."""
        url = f"{self._bridge_url}{path}"
        try:
            session = self._get_session()
            async with session.post(
                url, json=data, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                result: dict[str, Any] = await resp.json()
                return result
        except (TimeoutError, aiohttp.ClientError) as exc:
            msg = f"Bridge HTTP POST {path} failed: {exc}"
            raise MeshConnectionError(msg) from exc
