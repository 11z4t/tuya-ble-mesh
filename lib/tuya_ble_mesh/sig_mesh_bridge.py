"""SIG Mesh Bridge device — communicates via HTTP to BLE bridge daemon.

Used when the BLE adapter is on a different host (RPi) than Home Assistant.
Sends commands to the ``ble_mesh_daemon`` HTTP API and polls for results.

Provides the same duck-type interface as ``SIGMeshDevice`` for use with
``TuyaBLEMeshCoordinator``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from tuya_ble_mesh.exceptions import (
    ConnectionError as MeshConnectionError,
    SIGMeshError,
)

_LOGGER = logging.getLogger(__name__)

# Callback types (matching SIGMeshDevice interface)
OnOffCallback = Callable[[bool], Any]
VendorCallback = Callable[[int, bytes], Any]
DisconnectCallback = Callable[[], Any]

# Bridge daemon config
_DEFAULT_BRIDGE_PORT = 8099
_COMMAND_TIMEOUT = 60.0
_POLL_INTERVAL = 2.0
_MAX_POLL_ATTEMPTS = 30


class SIGMeshBridgeDevice:
    """SIG Mesh device controlled via BLE bridge daemon.

    Sends commands to a remote ``ble_mesh_daemon`` HTTP API and polls
    for results. The daemon handles WiFi toggling and BLE operations.
    """

    def __init__(
        self,
        address: str,
        target_addr: int,
        bridge_host: str,
        bridge_port: int = _DEFAULT_BRIDGE_PORT,
    ) -> None:
        """Initialize bridge device.

        Args:
            address: BLE MAC address of the mesh device.
            target_addr: Target unicast address in the mesh network.
            bridge_host: Hostname/IP of the RPi running ble_mesh_daemon.
            bridge_port: HTTP port of the bridge daemon.
        """
        self._address = address.upper()
        self._target_addr = target_addr
        self._bridge_host = bridge_host
        self._bridge_port = bridge_port
        self._bridge_url = f"http://{bridge_host}:{bridge_port}"

        self._connected = False
        self._firmware_version: str | None = None
        self._last_on_state: bool = False

        self._onoff_callbacks: list[OnOffCallback] = []
        self._vendor_callbacks: list[VendorCallback] = []
        self._disconnect_callbacks: list[DisconnectCallback] = []

    @property
    def address(self) -> str:
        """Return the device BLE MAC address."""
        return self._address

    @property
    def is_connected(self) -> bool:
        """Return True if the bridge daemon is reachable."""
        return self._connected

    @property
    def firmware_version(self) -> str | None:
        """Return firmware version string."""
        return self._firmware_version

    def register_onoff_callback(self, callback: OnOffCallback) -> None:
        """Register a GenericOnOff Status callback."""
        self._onoff_callbacks.append(callback)

    def unregister_onoff_callback(self, callback: OnOffCallback) -> None:
        """Remove an onoff callback."""
        self._onoff_callbacks.remove(callback)

    def register_vendor_callback(self, callback: VendorCallback) -> None:
        """Register a vendor message callback."""
        self._vendor_callbacks.append(callback)

    def unregister_vendor_callback(self, callback: VendorCallback) -> None:
        """Remove a vendor callback."""
        self._vendor_callbacks.remove(callback)

    def register_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Register a disconnect callback."""
        self._disconnect_callbacks.append(callback)

    def unregister_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Remove a disconnect callback."""
        self._disconnect_callbacks.remove(callback)

    async def connect(self, timeout: float = 10.0, max_retries: int = 3) -> None:
        """Verify bridge daemon is reachable.

        Args:
            timeout: HTTP request timeout.
            max_retries: Number of connection attempts.

        Raises:
            ConnectionError: If bridge daemon is not reachable.
        """
        for attempt in range(1, max_retries + 1):
            try:
                result = await self._http_get("/health", timeout=timeout)
                if result.get("status") == "ok":
                    self._connected = True
                    self._firmware_version = "bridge"
                    _LOGGER.info(
                        "Connected to BLE bridge at %s:%d",
                        self._bridge_host,
                        self._bridge_port,
                    )
                    return
            except Exception as exc:
                _LOGGER.warning(
                    "Bridge connection attempt %d/%d failed: %s",
                    attempt, max_retries, exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(2.0)

        msg = f"Bridge daemon not reachable at {self._bridge_url}"
        raise MeshConnectionError(msg)

    async def disconnect(self) -> None:
        """Mark as disconnected."""
        self._connected = False
        _LOGGER.info("Bridge device disconnected")

    async def send_power(self, on: bool) -> None:
        """Send GenericOnOff Set via bridge daemon.

        Submits command, then polls for result.

        Args:
            on: True to turn on, False to turn off.

        Raises:
            SIGMeshError: If command fails.
        """
        if not self._connected:
            msg = "Bridge not connected"
            raise SIGMeshError(msg)

        action = "on" if on else "off"
        result = await self._send_and_wait(action)

        if result.get("success"):
            status = result.get("status")
            on_state = status == "ON"
            self._last_on_state = on_state
            for callback in self._onoff_callbacks:
                try:
                    callback(on_state)
                except Exception:
                    _LOGGER.warning("OnOff callback error", exc_info=True)
        else:
            error = result.get("error", result.get("stderr", "Unknown error"))
            msg = f"Bridge command failed: {error}"
            raise SIGMeshError(msg)

    async def _send_and_wait(self, action: str) -> dict:
        """Submit a command to the bridge and poll for result.

        Args:
            action: Command action (on, off, status, setup).

        Returns:
            Result dict from the bridge daemon.
        """
        cmd = {
            "action": action,
            "target": f"{self._target_addr:04X}",
            "mac": self._address,
        }

        # Submit command
        await self._http_post("/command", cmd)
        _LOGGER.info("Command '%s' submitted to bridge", action)

        # Poll for result
        for attempt in range(_MAX_POLL_ATTEMPTS):
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                result = await self._http_get("/result")
                # Check if this is a fresh result (timestamp after we submitted)
                if result.get("action") == action and result.get("timestamp"):
                    return result
            except Exception:
                # Bridge might be down (WiFi off during BLE operation)
                _LOGGER.debug("Poll attempt %d — bridge unreachable (WiFi likely down)", attempt + 1)
                continue

        return {"success": False, "error": "Timed out waiting for bridge result"}

    async def _http_get(self, path: str, timeout: float = 5.0) -> dict:
        """Make an HTTP GET request to the bridge daemon."""
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._bridge_host, self._bridge_port),
            timeout=timeout,
        )
        try:
            request = f"GET {path} HTTP/1.1\r\nHost: {self._bridge_host}\r\n\r\n"
            writer.write(request.encode())
            await writer.drain()

            response = await asyncio.wait_for(reader.read(8192), timeout=timeout)
            body = self._parse_http_body(response.decode("utf-8", errors="replace"))
            return json.loads(body)
        finally:
            writer.close()

    async def _http_post(self, path: str, data: dict, timeout: float = 5.0) -> dict:
        """Make an HTTP POST request to the bridge daemon."""
        body = json.dumps(data)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._bridge_host, self._bridge_port),
            timeout=timeout,
        )
        try:
            request = (
                f"POST {path} HTTP/1.1\r\n"
                f"Host: {self._bridge_host}\r\n"
                f"Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                f"\r\n{body}"
            )
            writer.write(request.encode())
            await writer.drain()

            response = await asyncio.wait_for(reader.read(8192), timeout=timeout)
            resp_body = self._parse_http_body(response.decode("utf-8", errors="replace"))
            return json.loads(resp_body)
        finally:
            writer.close()

    @staticmethod
    def _parse_http_body(response: str) -> str:
        """Extract body from raw HTTP response."""
        parts = response.split("\r\n\r\n", 1)
        return parts[1] if len(parts) > 1 else "{}"
