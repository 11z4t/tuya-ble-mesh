"""Telink proprietary mesh device controlled via BLE bridge daemon.

Same bridge HTTP API as SIGMeshBridgeDevice but sends Telink-type
commands (power, brightness, color_temp, color) to the daemon.
Duck-types the MeshDevice interface for use with TuyaBLEMeshCoordinator.

After each successful command, fires a synthetic StatusResponse so
the coordinator updates HA entity state immediately.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from tuya_ble_mesh.const import (
    DEFAULT_BRIDGE_CONNECTION_TIMEOUT,
    DEFAULT_BRIDGE_MAX_RETRIES,
)
from tuya_ble_mesh.exceptions import (
    InvalidRequestError,
    MeshConnectionError,
    SIGMeshError,
)
from tuya_ble_mesh.logging_context import MeshLogAdapter, mesh_operation
from tuya_ble_mesh.sig_mesh_bridge_http import (
    BRIDGE_CONNECT_RETRY_DELAY,
    DEFAULT_MAX_RETRIES,
    MAX_POLL_ATTEMPTS,
    POLL_INTERVAL,
    RETRY_BACKOFF_MULTIPLIER,
    RETRY_INITIAL_BACKOFF,
    BridgeHTTPMixin,
    DEFAULT_BRIDGE_PORT,
)

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

# Callback types for Telink bridge (matching MeshDevice interface)
StatusCallback = Callable[[Any], Any]
DisconnectCallback = Callable[[], Any]


class TelinkBridgeDevice(BridgeHTTPMixin):
    """Telink proprietary mesh device controlled via BLE bridge daemon.

    Same bridge HTTP API as SIGMeshBridgeDevice but sends Telink-type
    commands (power, brightness, color_temp, color) to the daemon.
    Duck-types the MeshDevice interface for use with TuyaBLEMeshCoordinator.

    After each successful command, fires a synthetic StatusResponse so
    the coordinator updates HA entity state immediately.
    """

    def __init__(
        self,
        address: str,
        bridge_host: str,
        bridge_port: int = DEFAULT_BRIDGE_PORT,
    ) -> None:
        """Initialize Telink bridge device.

        Args:
            address: BLE MAC address of the mesh device.
            bridge_host: Hostname/IP of the RPi running ble_mesh_daemon.
                SECURITY: Must be on trusted network. No authentication.
            bridge_port: HTTP port of the bridge daemon.

        Raises:
            InvalidRequestError: If bridge_host contains CRLF characters (injection risk).
        """
        # SECURITY: Reject CRLF to prevent HTTP header injection
        if "\r" in bridge_host or "\n" in bridge_host:
            msg = "Invalid bridge_host: contains CRLF characters"
            raise InvalidRequestError(msg)

        self._address = address.upper()
        self._bridge_host = bridge_host
        self._bridge_port = bridge_port
        self._bridge_url = f"http://{bridge_host}:{bridge_port}"

        self._connected = False
        self._firmware_version: str | None = None
        self._mesh_id = 0
        self._session: aiohttp.ClientSession | None = None

        # Track last known state for synthetic status responses
        self._mode = 0  # 0=white, 1=color
        self._brightness = 0
        self._color_temp = 0
        self._color_brightness = 0
        self._red = 0
        self._green = 0
        self._blue = 0
        self._is_on = False
        self._cmd_lock = asyncio.Lock()

        self._status_callbacks: list[StatusCallback] = []
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
        """Return firmware version string (always 'bridge-telink' when connected)."""
        return self._firmware_version

    @property
    def mesh_id(self) -> int:
        """Return the target mesh address used in synthetic status responses."""
        return self._mesh_id

    @mesh_id.setter
    def mesh_id(self, value: int) -> None:
        """Set the target mesh address."""
        self._mesh_id = value

    def register_status_callback(self, callback: StatusCallback) -> None:
        """Register a callback for synthetic status updates.

        Args:
            callback: Called with a ``StatusResponse`` after each command.
        """
        self._status_callbacks.append(callback)

    def unregister_status_callback(self, callback: StatusCallback) -> None:
        """Remove a previously registered status callback.

        Args:
            callback: The callback to remove.
        """
        self._status_callbacks.remove(callback)

    def register_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Register a callback for disconnect events.

        Args:
            callback: Called when the bridge daemon times out.
        """
        self._disconnect_callbacks.append(callback)

    def unregister_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Remove a previously registered disconnect callback.

        Args:
            callback: The callback to remove.
        """
        self._disconnect_callbacks.remove(callback)

    async def connect(
        self,
        timeout: float = DEFAULT_BRIDGE_CONNECTION_TIMEOUT,
        max_retries: int = DEFAULT_BRIDGE_MAX_RETRIES,
    ) -> None:
        """Verify bridge daemon is reachable."""
        for attempt in range(1, max_retries + 1):
            try:
                result = await self._http_get("/health", timeout=timeout)
                if result.get("status") == "ok":
                    self._connected = True
                    self._firmware_version = "bridge-telink"
                    _LOGGER.info(
                        "Telink bridge connected at %s:%d",
                        self._bridge_host,
                        self._bridge_port,
                    )
                    return
            except (TimeoutError, aiohttp.ClientError, OSError) as exc:
                _LOGGER.warning(
                    "Bridge attempt %d/%d failed: %s",
                    attempt,
                    max_retries,
                    exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(BRIDGE_CONNECT_RETRY_DELAY)

        msg = f"Bridge daemon not reachable at {self._bridge_host}:{self._bridge_port}"
        raise MeshConnectionError(msg)

    async def disconnect(self) -> None:
        """Mark device as disconnected and close HTTP session."""
        self._connected = False
        await self._close_session()

    def _fire_disconnect(self) -> None:
        """Fire disconnect callbacks."""
        for callback in list(self._disconnect_callbacks):
            try:
                callback()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning("Disconnect callback error", exc_info=True)

    def _fire_status(self) -> None:
        """Fire status callbacks with current tracked state."""
        from tuya_ble_mesh.protocol import StatusResponse

        status = StatusResponse(
            mesh_id=self._mesh_id,
            mode=self._mode,
            white_brightness=self._brightness if self._is_on else 0,
            white_temp=self._color_temp,
            color_brightness=self._color_brightness if self._is_on else 0,
            red=self._red,
            green=self._green,
            blue=self._blue,
        )
        for callback in list(self._status_callbacks):
            try:
                callback(status)
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning("Status callback error", exc_info=True)

    async def send_power(self, on: bool) -> None:
        """Turn the device on or off via bridge daemon.

        Args:
            on: True to turn on, False to turn off.

        Raises:
            SIGMeshError: If bridge command fails or times out.
        """
        async with mesh_operation(self._address, "send_power"):
            action = "on" if on else "off"
            await self._send_telink_cmd(action)
        self._is_on = on
        if on and self._brightness == 0:
            self._brightness = 100  # Default brightness when turning on
        self._fire_status()

    async def send_brightness(self, level: int) -> None:
        """Set white brightness level via bridge daemon.

        Args:
            level: Brightness percentage (1-100).

        Raises:
            SIGMeshError: If bridge command fails or times out.
        """
        await self._send_telink_cmd("brightness", {"level": level})
        self._brightness = level
        self._is_on = True
        self._fire_status()

    async def send_color_temp(self, temp: int) -> None:
        """Set white color temperature via bridge daemon.

        Args:
            temp: Color temperature value (0-255).

        Raises:
            SIGMeshError: If bridge command fails or times out.
        """
        await self._send_telink_cmd("color_temp", {"temp": temp})
        self._color_temp = temp
        self._is_on = True
        self._fire_status()

    async def send_color(self, red: int, green: int, blue: int) -> None:
        """Set RGB color via bridge daemon.

        Args:
            red: Red channel (0-255).
            green: Green channel (0-255).
            blue: Blue channel (0-255).

        Raises:
            SIGMeshError: If bridge command fails or times out.
        """
        await self._send_telink_cmd("color", {"r": red, "g": green, "b": blue})
        self._red = red
        self._green = green
        self._blue = blue
        self._is_on = True
        self._fire_status()

    async def send_light_mode(self, mode: int) -> None:
        """Set light mode (0=white, 1=color) via bridge daemon.

        Args:
            mode: Light mode integer.

        Raises:
            SIGMeshError: If bridge command fails or times out.
        """
        await self._send_telink_cmd("light_mode", {"mode": mode})
        self._mode = mode
        self._fire_status()

    async def send_color_brightness(self, level: int) -> None:
        """Set color mode brightness via bridge daemon.

        Args:
            level: Color brightness value (0-255).

        Raises:
            SIGMeshError: If bridge command fails or times out.
        """
        await self._send_telink_cmd("color_brightness", {"level": level})
        self._color_brightness = level
        self._is_on = True
        self._fire_status()

    async def _send_telink_cmd(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> dict[str, Any]:
        """Send a Telink command via bridge, serialized by lock.

        Retries on transient failures with exponential backoff.

        Args:
            action: Command action string (on, off, brightness, etc.).
            params: Optional command parameters dict.
            max_retries: Maximum retry attempts (default 3).

        Returns:
            Result dict from the bridge daemon.

        Raises:
            SIGMeshError: If command fails after all retries or bridge disconnects.
        """
        if not self._connected:
            msg = "Bridge not connected"
            raise SIGMeshError(msg)

        backoff = RETRY_INITIAL_BACKOFF

        for attempt in range(1, max_retries + 1):
            try:
                result = await self._send_telink_cmd_once(action, params)
                return result
            except SIGMeshError:
                if attempt >= max_retries or not self._connected:
                    raise
                _LOGGER.warning(
                    "Telink command '%s' attempt %d/%d failed, retrying in %.1fs",
                    action,
                    attempt,
                    max_retries,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff *= RETRY_BACKOFF_MULTIPLIER

        # Should not reach here, but safety net
        msg = f"Telink command '{action}' failed after {max_retries} attempts"
        raise SIGMeshError(msg)

    async def _send_telink_cmd_once(
        self,
        action: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a single Telink command attempt via bridge, serialized by lock."""
        async with self._cmd_lock:
            cmd: dict[str, Any] = {
                "action": action,
                "mac": self._address,
                "device_type": "telink",
            }
            if params:
                cmd["params"] = params

            await self._http_post("/command", cmd)
            _LOGGER.info("Telink command '%s' submitted to bridge", action)

            for _ in range(MAX_POLL_ATTEMPTS):
                await asyncio.sleep(POLL_INTERVAL)
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
                except (TimeoutError, aiohttp.ClientError, OSError):
                    _LOGGER.debug("Poll — bridge unreachable (WiFi likely down)")
                    continue

            self._connected = False
            self._fire_disconnect()
            msg = "Timed out waiting for telink bridge result"
            raise SIGMeshError(msg)
