"""BLE Mesh Bridge devices — communicate via HTTP to BLE bridge daemon.

Used when the BLE adapter is on a different host (RPi) than Home Assistant.
Sends commands to the ``ble_mesh_daemon`` HTTP API and polls for results.

Provides two bridge device classes:
- ``SIGMeshBridgeDevice``: SIG Mesh plug (duck-type of SIGMeshDevice)
- ``TelinkBridgeDevice``: Telink proprietary mesh light (duck-type of MeshDevice)
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


# Callback types for Telink bridge (matching MeshDevice interface)
StatusCallback = Callable[[Any], Any]


class TelinkBridgeDevice:
    """Telink proprietary mesh device controlled via BLE bridge daemon.

    Same bridge HTTP API as SIGMeshBridgeDevice but sends Telink-type
    commands (power, brightness, color_temp, color) to the daemon.
    Duck-types the MeshDevice interface for use with TuyaBLEMeshCoordinator.
    """

    def __init__(
        self,
        address: str,
        bridge_host: str,
        bridge_port: int = _DEFAULT_BRIDGE_PORT,
    ) -> None:
        self._address = address.upper()
        self._bridge_host = bridge_host
        self._bridge_port = bridge_port

        self._connected = False
        self._firmware_version: str | None = None
        self._mesh_id = 0

        self._status_callbacks: list[StatusCallback] = []
        self._disconnect_callbacks: list[DisconnectCallback] = []

    @property
    def address(self) -> str:
        return self._address

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def firmware_version(self) -> str | None:
        return self._firmware_version

    @property
    def mesh_id(self) -> int:
        return self._mesh_id

    @mesh_id.setter
    def mesh_id(self, value: int) -> None:
        self._mesh_id = value

    def register_status_callback(self, callback: StatusCallback) -> None:
        self._status_callbacks.append(callback)

    def unregister_status_callback(self, callback: StatusCallback) -> None:
        self._status_callbacks.remove(callback)

    def register_disconnect_callback(self, callback: DisconnectCallback) -> None:
        self._disconnect_callbacks.append(callback)

    def unregister_disconnect_callback(self, callback: DisconnectCallback) -> None:
        self._disconnect_callbacks.remove(callback)

    async def connect(self, timeout: float = 10.0, max_retries: int = 3) -> None:
        """Verify bridge daemon is reachable."""
        for attempt in range(1, max_retries + 1):
            try:
                result = await self._http_get("/health", timeout=timeout)
                if result.get("status") == "ok":
                    self._connected = True
                    self._firmware_version = "bridge-telink"
                    _LOGGER.info(
                        "Telink bridge connected at %s:%d",
                        self._bridge_host, self._bridge_port,
                    )
                    return
            except Exception as exc:
                _LOGGER.warning(
                    "Bridge attempt %d/%d failed: %s",
                    attempt, max_retries, exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(2.0)

        msg = f"Bridge daemon not reachable at {self._bridge_host}:{self._bridge_port}"
        raise MeshConnectionError(msg)

    async def disconnect(self) -> None:
        self._connected = False

    async def send_power(self, on: bool) -> None:
        action = "on" if on else "off"
        await self._send_telink_cmd(action)

    async def send_brightness(self, level: int) -> None:
        await self._send_telink_cmd("brightness", {"level": level})

    async def send_color_temp(self, temp: int) -> None:
        await self._send_telink_cmd("color_temp", {"temp": temp})

    async def send_color(self, red: int, green: int, blue: int) -> None:
        await self._send_telink_cmd("color", {"r": red, "g": green, "b": blue})

    async def send_light_mode(self, mode: int) -> None:
        await self._send_telink_cmd("light_mode", {"mode": mode})

    async def send_color_brightness(self, level: int) -> None:
        await self._send_telink_cmd("color_brightness", {"level": level})

    async def _send_telink_cmd(
        self, action: str, params: dict[str, Any] | None = None,
    ) -> dict:
        if not self._connected:
            msg = "Bridge not connected"
            raise SIGMeshError(msg)

        cmd: dict[str, Any] = {
            "action": action,
            "mac": self._address,
            "device_type": "telink",
        }
        if params:
            cmd["params"] = params

        await self._http_post("/command", cmd)
        _LOGGER.info("Telink command '%s' submitted to bridge", action)

        for _ in range(_MAX_POLL_ATTEMPTS):
            await asyncio.sleep(_POLL_INTERVAL)
            try:
                result = await self._http_get("/result")
                if (
                    result.get("action") == action
                    and result.get("device_type") == "telink"
                    and result.get("timestamp")
                ):
                    if result.get("success"):
                        return result
                    error = result.get("error", "Unknown error")
                    msg = f"Telink command failed: {error}"
                    raise SIGMeshError(msg)
            except SIGMeshError:
                raise
            except Exception:
                _LOGGER.debug("Poll — bridge unreachable (WiFi likely down)")
                continue

        msg = "Timed out waiting for telink bridge result"
        raise SIGMeshError(msg)

    async def _http_get(self, path: str, timeout: float = 5.0) -> dict:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self._bridge_host, self._bridge_port),
            timeout=timeout,
        )
        try:
            request = f"GET {path} HTTP/1.1\r\nHost: {self._bridge_host}\r\n\r\n"
            writer.write(request.encode())
            await writer.drain()
            response = await asyncio.wait_for(reader.read(8192), timeout=timeout)
            body = SIGMeshBridgeDevice._parse_http_body(
                response.decode("utf-8", errors="replace"),
            )
            return json.loads(body)
        finally:
            writer.close()

    async def _http_post(
        self, path: str, data: dict, timeout: float = 5.0,
    ) -> dict:
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
            resp_body = SIGMeshBridgeDevice._parse_http_body(
                response.decode("utf-8", errors="replace"),
            )
            return json.loads(resp_body)
        finally:
            writer.close()
