"""High-level BLE mesh device command interface.

Provides ``MeshDevice`` for connecting to a Telink BLE mesh device,
provisioning it, sending encrypted commands, and receiving status
notifications via characteristic 1911.

Usage::

    device = MeshDevice("DC:23:4D:21:43:A5", b"out_of_mesh", b"123456")
    async with device:
        await device.send_power(True)
        await device.send_brightness(100)

SECURITY: Session keys and mesh credentials are NEVER logged.
Only operation names, lengths, and success/failure status are safe to log.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic

from tuya_ble_mesh.const import (
    TELINK_CHAR_COMMAND,
    TELINK_CHAR_STATUS,
    TELINK_CMD_COLOR,
    TELINK_CMD_COLOR_BRIGHTNESS,
    TELINK_CMD_LIGHT_MODE,
    TELINK_CMD_MESH_ADDRESS,
    TELINK_CMD_MESH_RESET,
    TELINK_CMD_POWER,
    TELINK_CMD_WHITE_BRIGHTNESS,
    TELINK_CMD_WHITE_TEMP,
)
from tuya_ble_mesh.exceptions import ConnectionError, ProtocolError
from tuya_ble_mesh.protocol import (
    StatusResponse,
    decode_status,
    decrypt_notification,
    encode_command_packet,
)
from tuya_ble_mesh.provisioner import provision
from tuya_ble_mesh.scanner import mac_to_bytes

_LOGGER = logging.getLogger(__name__)

# Mesh address 0xFFFF = broadcast to all devices
MESH_ADDRESS_ALL = 0xFFFF

# Sequence counter wraps at 24 bits
_MAX_SEQUENCE = 0xFFFFFF

# Status callback type
StatusCallback = Callable[[StatusResponse], Any]


class MeshDevice:
    """High-level interface to a Telink BLE mesh device.

    Manages connection, provisioning, command sending, and status
    notification handling.
    """

    def __init__(
        self,
        address: str,
        mesh_name: bytes,
        mesh_password: bytes,
        *,
        mesh_id: int = MESH_ADDRESS_ALL,
    ) -> None:
        """Initialize a mesh device interface.

        Args:
            address: BLE MAC address (e.g. ``DC:23:4D:21:43:A5``).
            mesh_name: Mesh network name (e.g. ``b"out_of_mesh"``).
            mesh_password: Mesh network password (e.g. ``b"123456"``).
            mesh_id: Target mesh address for commands (0xFFFF = broadcast).
        """
        self._address = address.upper()
        self._mesh_name = mesh_name
        self._mesh_password = mesh_password
        self._mesh_id = mesh_id
        self._mac_bytes = mac_to_bytes(address)
        self._client: BleakClient | None = None
        self._session_key: bytes | None = None
        self._sequence: int = 0
        self._status_callbacks: list[StatusCallback] = []
        self._connected = False

    @property
    def address(self) -> str:
        """Return the device BLE MAC address."""
        return self._address

    @property
    def mesh_id(self) -> int:
        """Return the target mesh address for commands."""
        return self._mesh_id

    @mesh_id.setter
    def mesh_id(self, value: int) -> None:
        """Set the target mesh address for commands."""
        if not 0 <= value <= 0xFFFF:
            msg = f"mesh_id must be 0..0xFFFF, got {value}"
            raise ProtocolError(msg)
        self._mesh_id = value

    @property
    def is_connected(self) -> bool:
        """Return True if the device is connected and provisioned."""
        return self._connected and self._session_key is not None

    def _next_sequence(self) -> int:
        """Get the next sequence number (24-bit, wrapping)."""
        seq = self._sequence
        self._sequence = (self._sequence + 1) & _MAX_SEQUENCE
        return seq

    def register_status_callback(self, callback: StatusCallback) -> None:
        """Register a callback for status notifications.

        Args:
            callback: Called with a StatusResponse when the device
                sends a status update via characteristic 1911.
        """
        self._status_callbacks.append(callback)

    def unregister_status_callback(self, callback: StatusCallback) -> None:
        """Remove a previously registered status callback.

        Args:
            callback: The callback to remove.
        """
        self._status_callbacks.remove(callback)

    def _handle_notification(self, _sender: BleakGATTCharacteristic, data: bytearray) -> None:
        """Handle a raw BLE notification from characteristic 1911.

        Decrypts the notification and dispatches to registered callbacks.
        """
        if self._session_key is None:
            _LOGGER.warning("Notification received but no session key")
            return

        try:
            decrypted = decrypt_notification(self._session_key, self._mac_bytes, bytes(data))
            status = decode_status(decrypted)
        except Exception:
            _LOGGER.warning("Failed to decode notification (%d bytes)", len(data), exc_info=True)
            return

        _LOGGER.debug(
            "Status: mode=%d bright=%d temp=%d",
            status.mode,
            status.white_brightness,
            status.white_temp,
        )

        for callback in self._status_callbacks:
            try:
                callback(status)
            except Exception:
                _LOGGER.warning("Status callback error", exc_info=True)

    async def connect(self, timeout: float = 30.0) -> None:
        """Connect to the BLE device and provision (pair).

        Args:
            timeout: Connection timeout in seconds.

        Raises:
            ConnectionError: If connection or provisioning fails.
        """
        if self._connected:
            _LOGGER.debug("Already connected to %s", self._address)
            return

        _LOGGER.info("Connecting to %s", self._address)

        try:
            self._client = BleakClient(self._address, timeout=timeout)
            await self._client.connect()
        except Exception as exc:
            self._client = None
            msg = f"Failed to connect to {self._address}"
            raise ConnectionError(msg) from exc

        try:
            self._session_key = await provision(self._client, self._mesh_name, self._mesh_password)
        except Exception as exc:
            await self._safe_disconnect()
            msg = f"Provisioning failed for {self._address}"
            raise ConnectionError(msg) from exc

        # Subscribe to status notifications
        try:
            await self._client.start_notify(TELINK_CHAR_STATUS, self._handle_notification)
        except Exception:
            _LOGGER.warning("Could not subscribe to notifications", exc_info=True)

        self._connected = True
        _LOGGER.info("Connected and provisioned: %s", self._address)

    async def disconnect(self) -> None:
        """Disconnect from the BLE device."""
        if not self._connected:
            return

        _LOGGER.info("Disconnecting from %s", self._address)
        await self._safe_disconnect()
        _LOGGER.info("Disconnected: %s", self._address)

    async def _safe_disconnect(self) -> None:
        """Disconnect without raising exceptions."""
        self._connected = False
        self._session_key = None
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                _LOGGER.debug("Disconnect error (ignored)", exc_info=True)
            self._client = None

    async def send_command(
        self,
        opcode: int,
        params: bytes,
        *,
        dest_id: int | None = None,
    ) -> None:
        """Send an encrypted command to the device.

        Args:
            opcode: Telink command code (e.g. 0xD0 for power).
            params: Command parameters.
            dest_id: Target mesh address (defaults to self.mesh_id).

        Raises:
            ConnectionError: If not connected.
        """
        if not self.is_connected or self._client is None or self._session_key is None:
            msg = "Not connected"
            raise ConnectionError(msg)

        target = dest_id if dest_id is not None else self._mesh_id
        seq = self._next_sequence()

        packet = encode_command_packet(
            self._session_key,
            self._mac_bytes,
            seq,
            target,
            opcode,
            params,
        )

        _LOGGER.debug(
            "Sending command 0x%02X (%d bytes) seq=%d to 0x%04X",
            opcode,
            len(packet),
            seq,
            target,
        )

        await self._client.write_gatt_char(TELINK_CHAR_COMMAND, packet, response=False)

    async def send_power(self, on: bool) -> None:
        """Turn the device on or off.

        Args:
            on: True to turn on, False to turn off.
        """
        params = b"\x01" if on else b"\x00"
        await self.send_command(TELINK_CMD_POWER, params)
        _LOGGER.info("Power %s sent to %s", "ON" if on else "OFF", self._address)

    async def send_brightness(self, level: int) -> None:
        """Set the white brightness level.

        Args:
            level: Brightness value (device-dependent range, typically 0-127).

        Raises:
            ProtocolError: If level is out of range.
        """
        if not 0 <= level <= 0xFF:
            msg = f"Brightness must be 0..255, got {level}"
            raise ProtocolError(msg)
        await self.send_command(TELINK_CMD_WHITE_BRIGHTNESS, bytes([level]))
        _LOGGER.info("Brightness %d sent to %s", level, self._address)

    async def send_color_temp(self, temp: int) -> None:
        """Set the white color temperature.

        Args:
            temp: Color temperature value (device-dependent range, typically 0-127).

        Raises:
            ProtocolError: If temp is out of range.
        """
        if not 0 <= temp <= 0xFF:
            msg = f"Color temp must be 0..255, got {temp}"
            raise ProtocolError(msg)
        await self.send_command(TELINK_CMD_WHITE_TEMP, bytes([temp]))
        _LOGGER.info("Color temp %d sent to %s", temp, self._address)

    async def send_color(self, red: int, green: int, blue: int) -> None:
        """Set the RGB color.

        Args:
            red: Red channel (0-255).
            green: Green channel (0-255).
            blue: Blue channel (0-255).

        Raises:
            ProtocolError: If any channel is out of range.
        """
        for name, val in [("red", red), ("green", green), ("blue", blue)]:
            if not 0 <= val <= 0xFF:
                msg = f"{name} must be 0..255, got {val}"
                raise ProtocolError(msg)
        await self.send_command(TELINK_CMD_COLOR, bytes([red, green, blue]))
        _LOGGER.info("Color (%d,%d,%d) sent to %s", red, green, blue, self._address)

    async def send_color_brightness(self, level: int) -> None:
        """Set the color mode brightness level.

        Args:
            level: Brightness value (0-255).

        Raises:
            ProtocolError: If level is out of range.
        """
        if not 0 <= level <= 0xFF:
            msg = f"Color brightness must be 0..255, got {level}"
            raise ProtocolError(msg)
        await self.send_command(TELINK_CMD_COLOR_BRIGHTNESS, bytes([level]))
        _LOGGER.info("Color brightness %d sent to %s", level, self._address)

    async def send_light_mode(self, mode: int) -> None:
        """Set the light mode.

        Args:
            mode: Light mode (0=white, 1=color, etc.).

        Raises:
            ProtocolError: If mode is out of range.
        """
        if not 0 <= mode <= 0xFF:
            msg = f"Light mode must be 0..255, got {mode}"
            raise ProtocolError(msg)
        await self.send_command(TELINK_CMD_LIGHT_MODE, bytes([mode]))
        _LOGGER.info("Light mode %d sent to %s", mode, self._address)

    async def send_mesh_address(self, new_address: int) -> None:
        """Assign a new mesh address to the device.

        Args:
            new_address: New mesh unicast address (1-0x7FFF).

        Raises:
            ProtocolError: If address is out of range.
        """
        if not 1 <= new_address <= 0x7FFF:
            msg = f"Mesh address must be 1..0x7FFF, got {new_address}"
            raise ProtocolError(msg)
        params = new_address.to_bytes(2, "little")
        await self.send_command(TELINK_CMD_MESH_ADDRESS, params)
        _LOGGER.info("Mesh address 0x%04X sent to %s", new_address, self._address)

    async def send_mesh_reset(self) -> None:
        """Reset the device mesh settings (remove from network)."""
        await self.send_command(TELINK_CMD_MESH_RESET, b"")
        _LOGGER.info("Mesh reset sent to %s", self._address)

    async def wait_for_status(self, timeout: float = 5.0) -> StatusResponse:
        """Wait for a single status notification.

        Args:
            timeout: Maximum wait time in seconds.

        Returns:
            The received status response.

        Raises:
            TimeoutError: If no status received within timeout.
        """
        from tuya_ble_mesh import exceptions as _exc

        event = asyncio.Event()
        result: list[StatusResponse] = []

        def on_status(status: StatusResponse) -> None:
            result.append(status)
            event.set()

        self.register_status_callback(on_status)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            msg = f"No status received within {timeout}s"
            raise _exc.TimeoutError(msg) from None
        finally:
            self.unregister_status_callback(on_status)

        return result[0]

    async def __aenter__(self) -> MeshDevice:
        """Async context manager entry — connect to device."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        """Async context manager exit — disconnect from device."""
        await self.disconnect()
