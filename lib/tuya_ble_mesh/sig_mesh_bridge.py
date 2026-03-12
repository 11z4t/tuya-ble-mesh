"""BLE Mesh Bridge devices — communicate via HTTP to BLE bridge daemon.

Used when the BLE adapter is on a different host (RPi) than Home Assistant.
Sends commands to the ``ble_mesh_daemon`` HTTP API and polls for results.

Provides two bridge device classes:
- ``SIGMeshBridgeDevice``: SIG Mesh plug (duck-type of SIGMeshDevice)
- ``TelinkBridgeDevice``: Telink proprietary mesh light (duck-type of MeshDevice)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from tuya_ble_mesh.exceptions import MeshConnectionError, SIGMeshError
from tuya_ble_mesh.logging_context import MeshLogAdapter, mesh_operation

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

# Callback types (matching SIGMeshDevice interface)
OnOffCallback = Callable[[bool], Any]
VendorCallback = Callable[[int, bytes], Any]
DisconnectCallback = Callable[[], Any]

# Bridge daemon config
_DEFAULT_BRIDGE_PORT = 8099
_COMMAND_TIMEOUT = 60.0
_POLL_INTERVAL = 2.0
_MAX_POLL_ATTEMPTS = 30

# Retry config for BLE write commands
_DEFAULT_MAX_RETRIES = 3
_RETRY_INITIAL_BACKOFF = 1.0
_RETRY_BACKOFF_MULTIPLIER = 2.0

# Delay between bridge connection retries (seconds)
_BRIDGE_CONNECT_RETRY_DELAY = 2.0


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

    async def _http_get(self, path: str, timeout: float = 5.0) -> dict[str, Any]:
        """Make an HTTP GET request to the bridge daemon."""
        url = f"{self._bridge_url}{path}"
        try:
            session = self._get_session()
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                result: dict[str, Any] = await resp.json()
                return result
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            msg = f"Bridge HTTP GET {path} failed: {exc}"
            raise MeshConnectionError(msg) from exc

    async def _http_post(
        self,
        path: str,
        data: dict[str, Any],
        timeout: float = 5.0,
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
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            msg = f"Bridge HTTP POST {path} failed: {exc}"
            raise MeshConnectionError(msg) from exc


class SIGMeshBridgeDevice(BridgeHTTPMixin):
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
                SECURITY: Must be on trusted network. No authentication.
            bridge_port: HTTP port of the bridge daemon.

        Raises:
            ValueError: If bridge_host contains CRLF characters (injection risk).
        """
        # SECURITY: Reject CRLF to prevent HTTP header injection
        if "\r" in bridge_host or "\n" in bridge_host:
            msg = "Invalid bridge_host: contains CRLF characters"
            raise ValueError(msg)

        self._address = address.upper()
        self._target_addr = target_addr
        self._bridge_host = bridge_host
        self._bridge_port = bridge_port
        self._bridge_url = f"http://{bridge_host}:{bridge_port}"

        self._connected = False
        self._firmware_version: str | None = None
        self._last_on_state: bool = False
        self._cmd_lock = asyncio.Lock()
        self._session: aiohttp.ClientSession | None = None

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
                    attempt,
                    max_retries,
                    exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(_BRIDGE_CONNECT_RETRY_DELAY)

        msg = f"Bridge daemon not reachable at {self._bridge_url}"
        raise MeshConnectionError(msg)

    async def disconnect(self) -> None:
        """Mark as disconnected and close HTTP session."""
        self._connected = False
        await self._close_session()
        _LOGGER.info("Bridge device disconnected")

    async def send_power(self, on: bool, *, max_retries: int = _DEFAULT_MAX_RETRIES) -> None:
        """Send GenericOnOff Set via bridge daemon with retry.

        Submits command, then polls for result. Serialized via lock
        to prevent command collisions on the bridge daemon. On failure,
        retries with exponential backoff up to max_retries times.

        Args:
            on: True to turn on, False to turn off.
            max_retries: Maximum number of retry attempts (default 3).

        Raises:
            SIGMeshError: If command fails after all retries.
        """
        if not self._connected:
            msg = "Bridge not connected"
            raise SIGMeshError(msg)

        async with self._cmd_lock, mesh_operation(self._address, "send_power"):
            action = "on" if on else "off"
            last_error: str = ""
            backoff = _RETRY_INITIAL_BACKOFF

            for attempt in range(1, max_retries + 1):
                try:
                    result = await self._send_and_wait(action)
                except SIGMeshError:
                    if attempt >= max_retries:
                        raise
                    _LOGGER.warning(
                        "Bridge command attempt %d/%d failed, retrying in %.1fs",
                        attempt,
                        max_retries,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    backoff *= _RETRY_BACKOFF_MULTIPLIER
                    continue

                if result.get("success"):
                    status = result.get("status")
                    on_state = status == "ON"
                    self._last_on_state = on_state
                    for callback in list(self._onoff_callbacks):
                        try:
                            callback(on_state)
                        except Exception:
                            _LOGGER.warning("OnOff callback error", exc_info=True)
                    return

                last_error = result.get("error", result.get("stderr", "Unknown error"))
                if attempt >= max_retries:
                    break

                _LOGGER.warning(
                    "Bridge command attempt %d/%d failed: %s, retrying in %.1fs",
                    attempt,
                    max_retries,
                    last_error,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff *= _RETRY_BACKOFF_MULTIPLIER

            msg = f"Bridge command failed after {max_retries} attempts: {last_error}"
            raise SIGMeshError(msg)

    async def _send_and_wait(self, action: str) -> dict[str, Any]:
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
                _LOGGER.debug(
                    "Poll attempt %d — bridge unreachable",
                    attempt + 1,
                )
                continue

        self._connected = False
        for callback in list(self._disconnect_callbacks):
            try:
                callback()
            except Exception:
                _LOGGER.warning("Disconnect callback error", exc_info=True)
        return {"success": False, "error": "Timed out waiting for bridge result"}

    # HTTP methods inherited from BridgeHTTPMixin


# Callback types for Telink bridge (matching MeshDevice interface)
StatusCallback = Callable[[Any], Any]


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
        bridge_port: int = _DEFAULT_BRIDGE_PORT,
    ) -> None:
        """Initialize Telink bridge device.

        Args:
            address: BLE MAC address of the mesh device.
            bridge_host: Hostname/IP of the RPi running ble_mesh_daemon.
                SECURITY: Must be on trusted network. No authentication.
            bridge_port: HTTP port of the bridge daemon.

        Raises:
            ValueError: If bridge_host contains CRLF characters (injection risk).
        """
        # SECURITY: Reject CRLF to prevent HTTP header injection
        if "\r" in bridge_host or "\n" in bridge_host:
            msg = "Invalid bridge_host: contains CRLF characters"
            raise ValueError(msg)

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
                        self._bridge_host,
                        self._bridge_port,
                    )
                    return
            except Exception as exc:
                _LOGGER.warning(
                    "Bridge attempt %d/%d failed: %s",
                    attempt,
                    max_retries,
                    exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(_BRIDGE_CONNECT_RETRY_DELAY)

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
        max_retries: int = _DEFAULT_MAX_RETRIES,
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

        backoff = _RETRY_INITIAL_BACKOFF

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
                backoff *= _RETRY_BACKOFF_MULTIPLIER

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

            self._connected = False
            self._fire_disconnect()
            msg = "Timed out waiting for telink bridge result"
            raise SIGMeshError(msg)

    # _http_get and _http_post inherited from BridgeHTTPMixin
