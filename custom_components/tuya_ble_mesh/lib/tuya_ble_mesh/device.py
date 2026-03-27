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

from bleak.backends.characteristic import BleakGATTCharacteristic

from tuya_ble_mesh.connection import BLEConnection
from tuya_ble_mesh.const import (
    DEFAULT_COMMAND_MAX_RETRIES,
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_MAX_RETRIES,
    DEFAULT_STATUS_WAIT_TIMEOUT,
    TELINK_VENDOR_ID,
)
from tuya_ble_mesh.device_commands import DeviceCommandsMixin
from tuya_ble_mesh.device_dispatcher import (
    _CommandDispatcher,
)
from tuya_ble_mesh.exceptions import (
    CryptoError,
    DisconnectedError,
    MalformedPacketError,
    MeshConnectionError,
    ProtocolError,
)
from tuya_ble_mesh.protocol import (
    StatusResponse,
    decode_status,
    decrypt_notification,
    encode_command_packet,
)
from tuya_ble_mesh.scanner import mac_to_bytes

_LOGGER = logging.getLogger(__name__)

# Mesh address for broadcast to all devices
MESH_ADDRESS_ALL = 0xFFFF

# Default mesh address for unprovisioned devices (not yet assigned)
MESH_ADDRESS_DEFAULT = 0

# Command retry backoff parameters
_COMMAND_RETRY_INITIAL_BACKOFF = 0.5
_COMMAND_RETRY_BACKOFF_MULTIPLIER = 2.0

# Status callback type
StatusCallback = Callable[[StatusResponse], Any]

# Disconnect callback type
DisconnectCallback = Callable[[], Any]


class MeshDevice(DeviceCommandsMixin):  # type: ignore[misc]
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
        return bool(self._conn.is_ready)

    @property
    def firmware_version(self) -> str | None:
        """Return the device firmware version, or None if not read.

        Returns:
            str | None: Device firmware version, or None if not read.
        """
        val = self._conn.firmware_version
        return str(val) if val is not None else None

    @property
    def notify_active(self) -> bool:
        """Return True if GATT push notifications are active (not poll-only).

        Returns:
            bool: True if GATT push notifications are active.
        """
        return bool(self._conn.notify_active)

    @property
    def rssi(self) -> int | None:
        """Return the current RSSI from the BLE connection, or None if not connected.

        RSSI (Received Signal Strength Indicator) is provided by the underlying
        BleakClient and represents the signal strength in dBm.

        Returns:
            int | None: RSSI in dBm, or None if not connected or unavailable.
        """
        val = self._conn.rssi
        return int(val) if val is not None else None

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
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning("Status callback error", exc_info=True)

    def _on_disconnect(self) -> None:
        """Handle disconnect from BLEConnection."""
        _LOGGER.warning("Device disconnected: %s", self._address)
        self._connected_event.clear()  # Signal dispatcher that device is no longer ready
        for callback in list(self._disconnect_callbacks):
            try:
                callback()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning("Disconnect callback error", exc_info=True)

    async def connect(
        self,
        timeout: float = DEFAULT_CONNECTION_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
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
        self,
        opcode: int,
        params: bytes,
        dest_id: int,
        *,
        max_retries: int = DEFAULT_COMMAND_MAX_RETRIES,
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
            except (MeshConnectionError, OSError) as exc:
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
        raise MeshConnectionError(msg)

    # --- High-level commands (0xD2 compact DP format) ---

    async def wait_for_status(self, timeout: float = DEFAULT_STATUS_WAIT_TIMEOUT) -> StatusResponse:
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
            """Store received status and signal the waiting coroutine.

            Args:
                status: The status response received from the device.
            """
            result.append(status)
            event.set()

        self.register_status_callback(on_status)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            msg = f"No status received within {timeout}s"
            raise _exc.MeshTimeoutError(msg) from None
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
