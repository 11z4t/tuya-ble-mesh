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
import time
from collections.abc import Callable
from typing import Any

from bleak.backends.characteristic import BleakGATTCharacteristic

from tuya_ble_mesh.connection import BLEConnection
from tuya_ble_mesh.const import (
    COMPACT_DP_BRIGHTNESS,
    COMPACT_DP_POWER,
    DP_TYPE_VALUE,
    TELINK_CMD_COLOR,
    TELINK_CMD_COLOR_BRIGHTNESS,
    TELINK_CMD_DP_WRITE,
    TELINK_CMD_LIGHT_MODE,
    TELINK_CMD_MESH_ADDRESS,
    TELINK_CMD_MESH_RESET,
    TELINK_CMD_WHITE_TEMP,
    TELINK_VENDOR_ID,
)
from tuya_ble_mesh.exceptions import (
    CommandExpiredError,
    CommandQueueFullError,
    DisconnectedError,
    ProtocolError,
)
from tuya_ble_mesh.protocol import (
    StatusResponse,
    decode_status,
    decrypt_notification,
    encode_command_packet,
    encode_compact_dp,
)
from tuya_ble_mesh.scanner import mac_to_bytes

_LOGGER = logging.getLogger(__name__)

# Mesh address for broadcast to all devices
MESH_ADDRESS_ALL = 0xFFFF

# Default mesh address for unprovisioned devices (not yet assigned)
MESH_ADDRESS_DEFAULT = 0

# Command queue limits
_QUEUE_MAX_SIZE = 32
_COMMAND_TTL = 60.0  # seconds

# Status callback type
StatusCallback = Callable[[StatusResponse], Any]

# Disconnect callback type
DisconnectCallback = Callable[[], Any]


class _QueuedCommand:
    """A command waiting in the queue."""

    __slots__ = ("created_at", "dest_id", "future", "opcode", "params")

    def __init__(
        self,
        opcode: int,
        params: bytes,
        dest_id: int,
        future: asyncio.Future[None],
    ) -> None:
        self.opcode = opcode
        self.params = params
        self.dest_id = dest_id
        self.created_at = time.monotonic()
        self.future = future


class MeshDevice:
    """High-level interface to a Telink BLE mesh device.

    Composes BLEConnection for transport. Provides command queue with
    TTL, high-level commands (0xD2 compact DP), and status notification
    dispatch.
    """

    def __init__(
        self,
        address: str,
        mesh_name: bytes,
        mesh_password: bytes,
        *,
        mesh_id: int = MESH_ADDRESS_DEFAULT,
        vendor_id: bytes = TELINK_VENDOR_ID,
        ble_device_callback: Any = None,
    ) -> None:
        """Initialize a mesh device interface.

        Args:
            address: BLE MAC address (e.g. ``DC:23:4D:21:43:A5``).
            mesh_name: Mesh network name (e.g. ``b"out_of_mesh"``).
            mesh_password: Mesh network password (e.g. ``b"123456"``).
            mesh_id: Target mesh address for commands (0 = unprovisioned default).
            vendor_id: 2-byte vendor identifier (default: TELINK_VENDOR_ID).
            ble_device_callback: Optional callback(address) → BLEDevice for
                HA Bluetooth Proxy support. If None, uses BleakScanner.
        """
        self._address = address.upper()
        self._mesh_id = mesh_id
        self._vendor_id = vendor_id
        self._mac_bytes = mac_to_bytes(address)
        self._conn = BLEConnection(
            address,
            mesh_name,
            mesh_password,
            vendor_id=vendor_id,
            ble_device_callback=ble_device_callback,
        )
        self._status_callbacks: list[StatusCallback] = []
        self._disconnect_callbacks: list[DisconnectCallback] = []
        self._queue: list[_QueuedCommand] = []
        self._conn.register_disconnect_callback(self._on_disconnect)

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
        return self._conn.is_ready

    @property
    def firmware_version(self) -> str | None:
        """Return the device firmware version, or None if not read."""
        return self._conn.firmware_version

    @property
    def connection(self) -> BLEConnection:
        """Return the underlying BLE connection."""
        return self._conn

    def register_status_callback(self, callback: StatusCallback) -> None:
        """Register a callback for status notifications."""
        self._status_callbacks.append(callback)

    def unregister_status_callback(self, callback: StatusCallback) -> None:
        """Remove a previously registered status callback."""
        self._status_callbacks.remove(callback)

    def register_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Register a callback for disconnect events."""
        self._disconnect_callbacks.append(callback)

    def unregister_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Remove a previously registered disconnect callback."""
        self._disconnect_callbacks.remove(callback)

    def _handle_notification(self, _sender: BleakGATTCharacteristic, data: bytearray) -> None:
        """Handle a raw BLE notification from characteristic 1911."""
        key = self._conn.session_key
        if key is None:
            _LOGGER.warning("Notification received but no session key")
            return

        try:
            decrypted = decrypt_notification(key, self._mac_bytes, bytes(data))
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

    def _on_disconnect(self) -> None:
        """Handle disconnect from BLEConnection."""
        _LOGGER.warning("Device disconnected: %s", self._address)
        for callback in self._disconnect_callbacks:
            try:
                callback()
            except Exception:
                _LOGGER.warning("Disconnect callback error", exc_info=True)

    async def connect(
        self,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> None:
        """Connect to the BLE device and provision (pair).

        After connecting, drains any queued commands.

        Args:
            timeout: Connection timeout in seconds per attempt.
            max_retries: Maximum connection attempts.

        Raises:
            ConnectionError: If connection or provisioning fails.
        """
        await self._conn.connect(timeout=timeout, max_retries=max_retries)
        await self._drain_queue()

    async def disconnect(self) -> None:
        """Disconnect from the BLE device."""
        await self._conn.disconnect()

    async def send_command(
        self,
        opcode: int,
        params: bytes,
        *,
        dest_id: int | None = None,
    ) -> None:
        """Send an encrypted command to the device.

        If the device is connected, sends immediately. Otherwise,
        queues the command for sending on reconnect.

        Args:
            opcode: Telink command code.
            params: Command parameters.
            dest_id: Target mesh address (defaults to self.mesh_id).

        Raises:
            CommandQueueFullError: If queue is full and device is not connected.
            DisconnectedError: If send fails and device disconnects.
        """
        target = dest_id if dest_id is not None else self._mesh_id

        if self.is_connected:
            await self._send_now(opcode, params, target)
        else:
            await self._enqueue(opcode, params, target)

    async def _send_now(self, opcode: int, params: bytes, dest_id: int) -> None:
        """Send a command immediately via BLEConnection."""
        key = self._conn.session_key
        if key is None:
            msg = "Not connected"
            raise DisconnectedError(msg)

        seq = self._conn.next_sequence()

        packet = encode_command_packet(
            key,
            self._mac_bytes,
            seq,
            dest_id,
            opcode,
            params,
            vendor_id=self._vendor_id,
        )

        _LOGGER.debug(
            "Sending command 0x%02X (%d bytes) seq=%d to 0x%04X",
            opcode,
            len(packet),
            seq,
            dest_id,
        )

        await self._conn.write_command(packet)

    async def _enqueue(self, opcode: int, params: bytes, dest_id: int) -> None:
        """Add a command to the queue for later sending.

        Raises:
            CommandQueueFullError: If queue is at capacity.
        """
        if len(self._queue) >= _QUEUE_MAX_SIZE:
            msg = f"Command queue full ({_QUEUE_MAX_SIZE})"
            raise CommandQueueFullError(msg)

        future: asyncio.Future[None] = asyncio.get_event_loop().create_future()
        cmd = _QueuedCommand(opcode, params, dest_id, future)
        self._queue.append(cmd)
        _LOGGER.debug("Queued command 0x%02X (queue size: %d)", opcode, len(self._queue))

        await future

    async def _drain_queue(self) -> None:
        """Send all queued commands that haven't expired."""
        if not self._queue:
            return

        _LOGGER.info("Draining command queue (%d commands)", len(self._queue))
        now = time.monotonic()
        remaining: list[_QueuedCommand] = []

        for cmd in self._queue:
            if now - cmd.created_at > _COMMAND_TTL:
                if not cmd.future.done():
                    cmd.future.set_exception(
                        CommandExpiredError(f"Command 0x{cmd.opcode:02X} expired")
                    )
                continue
            remaining.append(cmd)

        self._queue.clear()

        for cmd in remaining:
            try:
                await self._send_now(cmd.opcode, cmd.params, cmd.dest_id)
                if not cmd.future.done():
                    cmd.future.set_result(None)
            except Exception as exc:
                if not cmd.future.done():
                    cmd.future.set_exception(exc)
                break  # Stop draining on failure

    # --- High-level commands (0xD2 compact DP format) ---

    async def send_power(self, on: bool) -> None:
        """Turn the device on or off.

        Uses 0xD2 compact DP with dp_id 121 (confirmed from HCI snoop).

        Args:
            on: True to turn on, False to turn off.
        """
        params = encode_compact_dp(COMPACT_DP_POWER, DP_TYPE_VALUE, 1 if on else 0)
        await self.send_command(TELINK_CMD_DP_WRITE, params)
        _LOGGER.info("Power %s sent to %s", "ON" if on else "OFF", self._address)

    async def send_brightness(self, level: int) -> None:
        """Set the white brightness level.

        Uses 0xD2 compact DP with dp_id 122 (confirmed from HCI snoop).

        Args:
            level: Brightness percentage (1-100).

        Raises:
            ProtocolError: If level is out of range.
        """
        if not 1 <= level <= 100:
            msg = f"Brightness must be 1..100, got {level}"
            raise ProtocolError(msg)
        params = encode_compact_dp(COMPACT_DP_BRIGHTNESS, DP_TYPE_VALUE, level)
        await self.send_command(TELINK_CMD_DP_WRITE, params)
        _LOGGER.info("Brightness %d%% sent to %s", level, self._address)

    async def send_color_temp(self, temp: int) -> None:
        """Set the white color temperature.

        Args:
            temp: Color temperature value (0-255).

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
