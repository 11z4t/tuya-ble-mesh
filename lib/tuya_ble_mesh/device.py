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
import contextlib
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
    CommandQueueFullError,
    ConnectionError,
    CryptoError,
    DisconnectedError,
    MalformedPacketError,
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

# Command retry backoff parameters
_COMMAND_RETRY_INITIAL_BACKOFF = 0.5
_COMMAND_RETRY_BACKOFF_MULTIPLIER = 2.0

# Status callback type
StatusCallback = Callable[[StatusResponse], Any]

# Disconnect callback type
DisconnectCallback = Callable[[], Any]


class _QueuedCommand:
    """A command waiting in the queue."""

    __slots__ = ("created_at", "dest_id", "opcode", "params")

    def __init__(
        self,
        opcode: int,
        params: bytes,
        dest_id: int,
    ) -> None:
        self.opcode = opcode
        self.params = params
        self.dest_id = dest_id
        self.created_at = time.monotonic()


class _CommandDispatcher:
    """Async command dispatcher with internal worker task.

    Provides fire-and-forget enqueue-and-return semantics for HA callers.
    The dispatcher runs a separate asyncio worker task that drains the internal
    queue, respects TTL, and handles retry/cancellation/reconnect.
    """

    def __init__(
        self,
        device: MeshDevice,
        max_size: int = _QUEUE_MAX_SIZE,
        ttl: float = _COMMAND_TTL,
    ) -> None:
        """Initialize the command dispatcher.

        Args:
            device: Parent MeshDevice instance.
            max_size: Maximum queue size.
            ttl: Command time-to-live in seconds.
        """
        self._device = device
        self._max_size = max_size
        self._ttl = ttl
        self._queue: asyncio.Queue[_QueuedCommand] = asyncio.Queue(maxsize=max_size)
        self._worker_task: asyncio.Task[None] | None = None
        self._running = False

    def start(self) -> None:
        """Start the dispatcher worker task."""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker())
        self._worker_task.add_done_callback(self._log_worker_exception)
        _LOGGER.debug("Command dispatcher started")

    @staticmethod
    def _log_worker_exception(task: asyncio.Task[None]) -> None:
        """Log unhandled exceptions from the worker task."""
        if task.cancelled():
            return
        try:
            exc = task.exception()
            if exc is not None:
                _LOGGER.error("Command dispatcher worker crashed: %s", exc, exc_info=exc)
        except asyncio.CancelledError:
            pass

    async def stop(self) -> None:
        """Stop the dispatcher worker task and cancel pending commands."""
        if not self._running:
            return
        self._running = False
        if self._worker_task is not None:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
        # Drain any remaining items from the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        _LOGGER.debug("Command dispatcher stopped")

    async def enqueue(self, opcode: int, params: bytes, dest_id: int) -> None:
        """Enqueue a command for async sending (fire-and-forget).

        Args:
            opcode: Telink command code.
            params: Command parameters.
            dest_id: Target mesh address.

        Raises:
            CommandQueueFullError: If queue is at capacity.
        """
        if self._queue.full():
            msg = f"Command queue full ({self._max_size})"
            raise CommandQueueFullError(msg)
        cmd = _QueuedCommand(opcode, params, dest_id)
        await self._queue.put(cmd)
        _LOGGER.debug("Queued command 0x%02X (queue size: %d)", opcode, self._queue.qsize())

    async def _worker(self) -> None:
        """Worker task that drains the queue and sends commands."""
        _LOGGER.debug("Command dispatcher worker started")
        while self._running:
            try:
                # Wait for a command with a short timeout to allow checking _running
                try:
                    cmd = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                except TimeoutError:
                    continue

                # Check TTL
                age = time.monotonic() - cmd.created_at
                if age > self._ttl:
                    _LOGGER.warning(
                        "Command 0x%02X expired (age=%.1fs, TTL=%.1fs), dropping",
                        cmd.opcode,
                        age,
                        self._ttl,
                    )
                    self._queue.task_done()
                    continue

                # Wait for device to be ready (event-driven, no busy-wait)
                if not self._device.is_connected:
                    try:
                        # Wait for connection with periodic checks of _running flag
                        while self._running and not self._device.is_connected:
                            try:
                                await asyncio.wait_for(
                                    self._device._connected_event.wait(), timeout=1.0
                                )
                            except TimeoutError:
                                # Timeout allows checking _running flag periodically
                                continue
                    except asyncio.CancelledError:
                        self._queue.task_done()
                        raise

                if not self._running:
                    self._queue.task_done()
                    break

                # Send the command
                try:
                    await self._device._send_now(cmd.opcode, cmd.params, cmd.dest_id)
                except (ConnectionError, DisconnectedError):
                    _LOGGER.warning(
                        "Command 0x%02X send failed, dropping",
                        cmd.opcode,
                        exc_info=True,
                    )

                self._queue.task_done()

            except asyncio.CancelledError:
                _LOGGER.debug("Command dispatcher worker cancelled")
                raise
            except BaseException:
                _LOGGER.error("Command dispatcher worker error", exc_info=True)

        _LOGGER.debug("Command dispatcher worker stopped")


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
        adapter: str | None = None,
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
            adapter: BLE adapter name (e.g. "hci0"). Forces scan and connect
                via this specific adapter, bypassing HA's habluetooth routing.
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
            adapter=adapter,
        )
        self._status_callbacks: list[StatusCallback] = []
        self._disconnect_callbacks: list[DisconnectCallback] = []
        self._dispatcher = _CommandDispatcher(self)
        # Event to signal connection state changes (for dispatcher worker)
        self._connected_event = asyncio.Event()
        # Wire up notification and disconnect callbacks to BLEConnection
        self._conn.set_notification_handler(self._handle_notification)
        self._conn.register_disconnect_callback(self._on_disconnect)

    @property
    def address(self) -> str:
        """Return the device BLE MAC address.

        Returns:
            str: Device BLE MAC address.
        """
        return self._address

    @property
    def mesh_id(self) -> int:
        """Return the target mesh address for commands.

        Returns:
            int: Target mesh address for commands.
        """
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
        """Return True if the device is connected and provisioned.

        Returns:
            bool: True if device is connected and provisioned.
        """
        return self._conn.is_ready

    @property
    def firmware_version(self) -> str | None:
        """Return the device firmware version, or None if not read.

        Returns:
            str | None: Device firmware version, or None if not read.
        """
        return self._conn.firmware_version

    @property
    def notify_active(self) -> bool:
        """Return True if GATT push notifications are active (not poll-only).

        Returns:
            bool: True if GATT push notifications are active.
        """
        return self._conn.notify_active

    @property
    def connection(self) -> BLEConnection:
        """Return the underlying BLE connection.

        Returns:
            BLEConnection: Underlying BLE connection.
        """
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
        """Handle a raw BLE notification from characteristic 1911.

        Called by Bleak (possibly from a worker thread).  We take an
        immutable snapshot of the session key first so that a concurrent
        disconnect/cleanup cannot zero the key mid-decrypt.
        """
        # Atomic snapshot: session_key property returns immutable bytes or None
        key: bytes | None = self._conn.session_key
        if key is None:
            _LOGGER.debug("Notification dropped: no session key (device disconnecting?)")
            return

        try:
            decrypted = decrypt_notification(key, self._mac_bytes, bytes(data))
            status = decode_status(decrypted)
        except (CryptoError, MalformedPacketError):
            _LOGGER.warning("Failed to decode notification (%d bytes)", len(data), exc_info=True)
            return

        _LOGGER.debug(
            "Status: mode=%d bright=%d temp=%d",
            status.mode,
            status.white_brightness,
            status.white_temp,
        )

        for callback in list(self._status_callbacks):
            try:
                callback(status)
            except BaseException:
                _LOGGER.warning("Status callback error", exc_info=True)

    def _on_disconnect(self) -> None:
        """Handle disconnect from BLEConnection."""
        _LOGGER.warning("Device disconnected: %s", self._address)
        self._connected_event.clear()  # Signal dispatcher that device is no longer ready
        for callback in list(self._disconnect_callbacks):
            try:
                callback()
            except BaseException:
                _LOGGER.warning("Disconnect callback error", exc_info=True)

    async def connect(
        self,
        timeout: float = 30.0,
        max_retries: int = 5,
    ) -> None:
        """Connect to the BLE device and provision (pair).

        After connecting, starts the command dispatcher worker.

        Args:
            timeout: Connection timeout in seconds per attempt.
            max_retries: Maximum connection attempts.

        Raises:
            ConnectionError: If connection or provisioning fails.
        """
        await self._conn.connect(timeout=timeout, max_retries=max_retries)
        self._connected_event.set()  # Signal dispatcher that device is ready
        self._dispatcher.start()

    async def disconnect(self) -> None:
        """Disconnect from the BLE device and stop the dispatcher."""
        await self._dispatcher.stop()
        await self._conn.disconnect()

    async def send_command(
        self,
        opcode: int,
        params: bytes,
        *,
        dest_id: int | None = None,
    ) -> None:
        """Send an encrypted command to the device (fire-and-forget).

        Commands are enqueued in the dispatcher's async queue. The dispatcher
        worker task handles TTL, retry, and connection state internally.

        Args:
            opcode: Telink command code.
            params: Command parameters.
            dest_id: Target mesh address (defaults to self.mesh_id).

        Raises:
            CommandQueueFullError: If queue is full.
        """
        target = dest_id if dest_id is not None else self._mesh_id
        await self._dispatcher.enqueue(opcode, params, target)

    async def _send_now(
        self, opcode: int, params: bytes, dest_id: int, *, max_retries: int = 3
    ) -> None:
        """Send a command immediately via BLEConnection with retry.

        Retries on transient BLE write failures with exponential backoff.

        Args:
            opcode: Telink command code.
            params: Command parameters.
            dest_id: Target mesh address.
            max_retries: Maximum retry attempts (default 3).

        Raises:
            DisconnectedError: If not connected.
            ConnectionError: If BLE write fails after all retries.
        """
        last_error: Exception | None = None
        backoff = _COMMAND_RETRY_INITIAL_BACKOFF

        for attempt in range(1, max_retries + 1):
            key = self._conn.session_key
            if key is None:
                msg = "Not connected"
                raise DisconnectedError(msg)

            seq = await self._conn.next_sequence()

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
                "Sending command 0x%02X (%d bytes) seq=%d to 0x%04X (attempt %d/%d)",
                opcode,
                len(packet),
                seq,
                dest_id,
                attempt,
                max_retries,
            )

            try:
                await self._conn.write_command(packet)
                return
            except DisconnectedError:
                raise
            except (ConnectionError, OSError) as exc:
                last_error = exc
                if attempt >= max_retries:
                    break
                _LOGGER.warning(
                    "BLE write attempt %d/%d failed for 0x%02X: %s, retrying in %.1fs",
                    attempt,
                    max_retries,
                    opcode,
                    type(exc).__name__,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff *= _COMMAND_RETRY_BACKOFF_MULTIPLIER

        if last_error is not None:
            raise last_error
        msg = f"Command 0x{opcode:02X} failed after {max_retries} attempts"
        raise ConnectionError(msg)

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
