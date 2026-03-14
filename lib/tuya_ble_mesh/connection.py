"""BLE connection manager for Telink mesh devices.

Manages the BLE transport lifecycle including connect/disconnect with retry,
keep-alive, disconnect detection, and session key storage.

SECURITY: Session keys are zero-filled before clearing references on disconnect.
Key material is NEVER logged — only operation names and lengths.
"""

from __future__ import annotations

import asyncio
import contextlib  # CF-3: For suppress in _stop_keep_alive
import enum
import logging
import random
from collections.abc import Callable
from typing import Any

from bleak import BleakClient, BleakError, BleakScanner
from bleak_retry_connector import establish_connection

from tuya_ble_mesh.const import (
    DIS_FIRMWARE_REVISION,
    TELINK_CHAR_COMMAND,
    TELINK_CHAR_STATUS,
    TELINK_CMD_STATUS_QUERY,
    TELINK_VENDOR_ID,
)
from tuya_ble_mesh.exceptions import DisconnectedError, MeshConnectionError, ProvisioningError
from tuya_ble_mesh.protocol import encode_command_packet
from tuya_ble_mesh.provisioner import provision
from tuya_ble_mesh.scanner import mac_to_bytes

_LOGGER = logging.getLogger(__name__)

# Keep-alive interval (device drops connection at ~60s)
KEEP_ALIVE_INTERVAL = 30.0

# Status query param (0x10 confirmed from HCI snoop)
_STATUS_QUERY_PARAM = b"\x10"

# Reconnect backoff parameters
_INITIAL_BACKOFF = 5.0
_MAX_BACKOFF = 300.0
_BACKOFF_MULTIPLIER = 2.0
_JITTER_FACTOR = 0.2  # 0-20% random jitter

# Max connection retries per attempt
_DEFAULT_MAX_RETRIES = 5

# Sequence counter wraps at 24 bits
_MAX_SEQUENCE = 0xFFFFFF

# Disconnect callback type
DisconnectCallback = Callable[[], Any]


class ConnectionState(enum.Enum):
    """BLE connection state machine states.

    State transitions (PLAT-402 Phase 1 Task 1.3 extended)::

        DISCONNECTED ──connect()──→ CONNECTING
        CONNECTING ──BLE success──→ PAIRING
        PAIRING ──session key──→ READY
        READY ──disconnect detected──→ DISCONNECTING
        DISCONNECTING ──cleanup done──→ DISCONNECTED

        CONNECTING ──all retries failed──→ DISCONNECTED
        PAIRING ──provision failed──→ DISCONNECTED

        Task 1.3: Extended states for degraded connections:
        READY ──X failed writes──→ DEGRADED
        DEGRADED ──reconnect initiated──→ RECOVERING
        RECOVERING ──reconnect success──→ READY
        RECOVERING ──reconnect failed──→ DISCONNECTED
    """

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    PAIRING = "pairing"
    READY = "ready"
    DISCONNECTING = "disconnecting"
    # PLAT-402 Task 1.3: Degraded connection states
    DEGRADED = "degraded"  # Connection alive but unreliable (X failed writes)
    RECOVERING = "recovering"  # Active reconnect in progress


class BLEConnection:
    """Manages BLE transport to a single Telink mesh device.

    Handles connect/disconnect with retry, keep-alive probes,
    disconnect detection, and session key lifecycle.
    """

    def __init__(
        self,
        address: str,
        mesh_name: bytes,
        mesh_password: bytes,
        *,
        vendor_id: bytes = TELINK_VENDOR_ID,
        ble_device_callback: Callable[[str], Any] | None = None,
        adapter: str | None = None,
    ) -> None:
        self._address = address.upper()
        self._mac_bytes = mac_to_bytes(address)
        self._mesh_name = mesh_name
        self._mesh_password = mesh_password
        self._vendor_id = vendor_id
        self._ble_device_callback = ble_device_callback
        self._adapter = adapter
        self._state = ConnectionState.DISCONNECTED
        self._client: BleakClient | None = None
        self._session_key: bytearray | None = None
        self._firmware_version: str | None = None
        self._sequence: int = 0
        self._sequence_lock = asyncio.Lock()
        self._keep_alive_task: asyncio.Task[None] | None = None
        self._disconnect_callbacks: list[DisconnectCallback] = []
        self._notification_handler: Callable[..., Any] | None = None
        # True if start_notify succeeded; False = poll-only mode
        self._notify_active: bool = False

    @property
    def state(self) -> ConnectionState:
        """Return the current connection state."""
        return self._state

    @property
    def address(self) -> str:
        """Return the device BLE MAC address."""
        return self._address

    @property
    def session_key(self) -> bytes | None:
        """Return a copy of the session key, or None if not connected."""
        if self._session_key is None:
            return None
        return bytes(self._session_key)

    @property
    def is_ready(self) -> bool:
        """Return True if the connection is ready for commands."""
        return self._state == ConnectionState.READY

    @property
    def notify_active(self) -> bool:
        """Return True if GATT notification subscription is active.

        False means the connection is in poll-only mode — status updates
        only arrive via keep-alive status-query responses.
        """
        return self._notify_active

    @property
    def firmware_version(self) -> str | None:
        """Return the device firmware version, or None if not read."""
        return self._firmware_version

    async def next_sequence(self) -> int:
        """Get the next sequence number (24-bit, wrapping).

        Protected by asyncio.Lock to prevent nonce collision from
        concurrent callers.
        """
        async with self._sequence_lock:
            seq = self._sequence
            self._sequence = (self._sequence + 1) & _MAX_SEQUENCE
            return seq

    def register_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Register a callback for disconnect events."""
        self._disconnect_callbacks.append(callback)

    def unregister_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Remove a previously registered disconnect callback."""
        self._disconnect_callbacks.remove(callback)

    def set_notification_handler(self, handler: Callable[..., Any] | None) -> None:
        """Set the handler for BLE notifications from char 1911."""
        self._notification_handler = handler

    async def _start_notify_safe(self) -> bool:
        """Subscribe to TELINK_CHAR_STATUS GATT notifications.

        Telink BLE mesh devices do NOT support standard CCCD writes via
        ``start_notify`` on BlueZ ≤ 5.83 (triggers EOFError that kills the
        BleakClient).  We call ``start_notify`` anyway — on newer BlueZ and
        non-Linux backends it works correctly.  On older BlueZ we catch the
        error and fall back to *poll-only mode* where state updates arrive
        exclusively via keep-alive status-query responses.

        Returns:
            ``True`` if GATT notification subscription is active.
            ``False`` if fell back to poll-only mode.
        """
        if self._client is None or self._notification_handler is None:
            return False

        try:
            await self._client.start_notify(TELINK_CHAR_STATUS, self._notification_handler)
            self._notify_active = True
            _LOGGER.info(
                "GATT notification subscription active for %s (push mode)",
                self._address,
            )
        except OSError as exc:
            self._notify_active = False
            _LOGGER.warning(
                "start_notify failed for %s (%s) — running in poll-only mode. "
                "Status updates arrive via keep-alive queries only.",
                self._address,
                type(exc).__name__,
            )

        return self._notify_active

    async def connect(
        self,
        timeout: float = 30.0,
        max_retries: int = _DEFAULT_MAX_RETRIES,
    ) -> None:
        """Connect to the BLE device and provision (pair).

        Args:
            timeout: Connection timeout per attempt.
            max_retries: Maximum connection attempts.

        Raises:
            MeshConnectionError: If connection or provisioning fails.
        """
        if self._state == ConnectionState.READY:
            _LOGGER.debug("Already connected to %s", self._address)
            return

        self._state = ConnectionState.CONNECTING
        _LOGGER.info("Connecting to %s", self._address)

        try:
            await self._connect_with_retry(timeout, max_retries)
        except (TimeoutError, OSError):
            self._state = ConnectionState.DISCONNECTED
            raise

        if self._client is None:
            self._state = ConnectionState.DISCONNECTED
            msg = "BLE client not set after connect"
            raise MeshConnectionError(msg)

        self._state = ConnectionState.PAIRING

        try:
            key = await provision(
                self._client,
                self._mesh_name,
                self._mesh_password,
            )
            self._session_key = bytearray(key)
        except (TimeoutError, ProvisioningError, OSError) as exc:
            await self._cleanup()
            msg = f"Provisioning failed for {self._address}"
            raise MeshConnectionError(msg) from exc

        await self._read_firmware_version()

        self._state = ConnectionState.READY
        await self._start_keep_alive()  # CF-3: Now awaited
        await self._start_notify_safe()
        _LOGGER.info("Connected and provisioned: %s", self._address)

    async def _connect_with_retry(
        self,
        timeout: float,
        max_retries: int,
    ) -> None:
        """Connect to BLE device using bleak-retry-connector."""
        last_exc: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                if self._ble_device_callback is not None:
                    ble_device = self._ble_device_callback(self._address)
                else:
                    scan_kwargs: dict[str, Any] = {"timeout": timeout}
                    if self._adapter is not None:
                        scan_kwargs["adapter"] = self._adapter
                    _LOGGER.debug(
                        "Scanning for %s (adapter=%s)",
                        self._address,
                        self._adapter or "default",
                    )
                    ble_device = await BleakScanner.find_device_by_address(
                        self._address,
                        **scan_kwargs,
                    )
                if ble_device is None:
                    msg = (
                        f"Device {self._address} not found in BLE scan. "
                        "Ensure device is powered on and in range of a BLE adapter "
                        "or ESPHome proxy."
                    )
                    raise MeshConnectionError(msg)

                self._client = await establish_connection(
                    BleakClient,
                    ble_device,
                    self._address,
                    max_attempts=3,
                )
                _LOGGER.info(
                    "BLE connected on attempt %d/%d",
                    attempt,
                    max_retries,
                )
                return
            except MeshConnectionError:
                self._client = None
                raise
            except (OSError, TimeoutError, BleakError, asyncio.CancelledError) as exc:
                last_exc = exc if isinstance(exc, Exception) else Exception(str(exc))
                self._client = None
                backoff = min(2.0 * attempt, 8.0)
                _LOGGER.warning(
                    "Connect attempt %d/%d failed (%s), retrying in %.1fs",
                    attempt,
                    max_retries,
                    type(exc).__name__,
                    backoff,
                )
                await asyncio.sleep(backoff)

        msg = f"Failed to connect to {self._address} after {max_retries} attempts"
        raise MeshConnectionError(msg) from last_exc

    async def _read_firmware_version(self) -> None:
        """Read firmware version from Device Information Service (0x2A26)."""
        if self._client is None:
            return
        try:
            raw = await self._client.read_gatt_char(DIS_FIRMWARE_REVISION)
            self._firmware_version = raw.decode("utf-8", errors="replace").strip()
            _LOGGER.debug("Firmware version: %s", self._firmware_version)
        except OSError:
            _LOGGER.debug("Could not read firmware version (ignored)", exc_info=True)

    async def _clear_bluez_device(self) -> None:
        """Remove the device from BlueZ D-Bus cache."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                "remove",
                self._address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (TimeoutError, OSError):
            _LOGGER.debug("bluetoothctl remove failed (ignored)", exc_info=True)

    async def disconnect(self) -> None:
        """Disconnect from the BLE device."""
        if self._state == ConnectionState.DISCONNECTED:
            return

        _LOGGER.info("Disconnecting from %s", self._address)
        self._state = ConnectionState.DISCONNECTING
        await self._cleanup()
        _LOGGER.info("Disconnected: %s", self._address)

    async def _cleanup(self) -> None:
        """Clean up resources and transition to DISCONNECTED."""
        await self._stop_keep_alive()  # CF-3: Now awaited for proper cleanup
        self._notify_active = False

        # Zero-fill session key before clearing
        if self._session_key is not None:
            for i in range(len(self._session_key)):
                self._session_key[i] = 0
            self._session_key = None

        if self._client is not None:
            try:
                await self._client.disconnect()
            except OSError:
                _LOGGER.debug("Disconnect error (ignored)", exc_info=True)
            self._client = None

        self._state = ConnectionState.DISCONNECTED

    async def write_command(self, packet: bytes) -> None:
        """Write a command packet to characteristic 1912.

        Args:
            packet: 20-byte encrypted command packet.

        Raises:
            DisconnectedError: If not in READY state.
            MeshConnectionError: If write fails (triggers disconnect detection).
        """
        if self._state != ConnectionState.READY or self._client is None:
            msg = "Not connected"
            raise DisconnectedError(msg)

        try:
            await self._client.write_gatt_char(TELINK_CHAR_COMMAND, packet, response=False)
        except OSError as exc:
            _LOGGER.warning("Write failed, triggering disconnect: %s", type(exc).__name__)
            await self._handle_disconnect()
            msg = f"Write failed to {self._address}"
            raise MeshConnectionError(msg) from exc

        # Reset keep-alive timer on successful write
        await self._restart_keep_alive()  # CF-3: Now awaited

    async def _handle_disconnect(self) -> None:
        """Handle an unexpected disconnect."""
        if self._state == ConnectionState.DISCONNECTING:
            return  # Already disconnecting

        _LOGGER.warning("Disconnect detected for %s", self._address)
        await self._cleanup()

        for callback in self._disconnect_callbacks:
            try:
                callback()
            except BaseException:
                _LOGGER.warning("Disconnect callback error", exc_info=True)

    # --- Keep-alive ---

    async def _start_keep_alive(self) -> None:
        """Start the keep-alive timer.

        CF-3: Now async to properly await _stop_keep_alive.
        """
        await self._stop_keep_alive()  # CF-3: Await for clean shutdown
        self._keep_alive_task = asyncio.ensure_future(self._keep_alive_loop())

    async def _stop_keep_alive(self) -> None:
        """Stop the keep-alive timer.

        CF-3: Now async to properly await task cancellation and prevent resource leak.
        """
        if self._keep_alive_task is not None:
            self._keep_alive_task.cancel()
            # CF-3: Await task to ensure clean cancellation without ResourceWarning
            with contextlib.suppress(asyncio.CancelledError):
                await self._keep_alive_task
            self._keep_alive_task = None

    async def _restart_keep_alive(self) -> None:
        """Reset the keep-alive timer (called after real commands).

        CF-3: Now async to properly await _start_keep_alive.
        """
        if self._state == ConnectionState.READY:
            await self._start_keep_alive()  # CF-3: Await for proper cleanup

    async def _keep_alive_loop(self) -> None:
        """Periodically send 0xDA status query to keep connection alive."""
        try:
            while self._state == ConnectionState.READY:
                await asyncio.sleep(KEEP_ALIVE_INTERVAL)
                if self._state != ConnectionState.READY:
                    break
                await self._send_keep_alive()
        except asyncio.CancelledError:
            pass

    async def _send_keep_alive(self) -> None:
        """Send a single keep-alive probe."""
        if (
            self._state != ConnectionState.READY
            or self._client is None
            or self._session_key is None
        ):
            return

        try:
            seq = await self.next_sequence()
            packet = encode_command_packet(
                bytes(self._session_key),
                self._mac_bytes,
                seq,
                0xFFFF,  # broadcast
                TELINK_CMD_STATUS_QUERY,
                _STATUS_QUERY_PARAM,
                vendor_id=self._vendor_id,
            )
            await self._client.write_gatt_char(TELINK_CHAR_COMMAND, packet, response=False)
            _LOGGER.debug("Keep-alive sent (seq=%d)", seq)
        except OSError:
            _LOGGER.warning("Keep-alive failed, triggering disconnect")
            await self._handle_disconnect()

    # --- Backoff calculation ---

    @staticmethod
    def calculate_backoff(current_backoff: float) -> float:
        """Calculate the next reconnect backoff with jitter.

        Args:
            current_backoff: Current backoff interval.

        Returns:
            Next backoff value (capped at _MAX_BACKOFF, with jitter).
        """
        next_val = min(current_backoff * _BACKOFF_MULTIPLIER, _MAX_BACKOFF)
        jitter = next_val * _JITTER_FACTOR * random.random()  # nosec B311
        return next_val + jitter
