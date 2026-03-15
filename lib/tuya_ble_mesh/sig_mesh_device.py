"""High-level SIG Mesh device interface for GATT Proxy control.

Provides ``SIGMeshDevice`` for connecting to a standard Bluetooth SIG Mesh
device via GATT Proxy (UUID 0x1828), sending GenericOnOff commands, and
receiving status notifications.

Key material is loaded from 1Password via ``SecretsManager`` (Rule S10).
All byte parsing is delegated to ``sig_mesh_protocol`` (Rule S3).
All crypto operations are in ``sig_mesh_crypto`` (Rule S4).

SECURITY: Key material is NEVER logged, printed, or included in exceptions.
Only addresses, lengths, and opcodes are safe to log.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakDBusError, BleakError

from tuya_ble_mesh.const import (
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_MAX_RETRIES,
)
from tuya_ble_mesh.exceptions import (
    ConnectionError as MeshConnectionError,
)
from tuya_ble_mesh.exceptions import (
    SIGMeshError,
)
from tuya_ble_mesh.logging_context import MeshLogAdapter, mesh_operation
from tuya_ble_mesh.sig_mesh_device_commands import SIGMeshDeviceCommandsMixin
from tuya_ble_mesh.sig_mesh_device_notify import SIGMeshDeviceNotifyMixin
from tuya_ble_mesh.sig_mesh_protocol import (
    CompositionData,
    MeshKeys,
)

_LOGGER = MeshLogAdapter(logging.getLogger(__name__), {})

# SIG Mesh GATT Proxy UUIDs
SIG_MESH_PROXY_SERVICE = "00001828-0000-1000-8000-00805f9b34fb"
SIG_MESH_PROXY_DATA_IN = "00002add-0000-1000-8000-00805f9b34fb"
SIG_MESH_PROXY_DATA_OUT = "00002ade-0000-1000-8000-00805f9b34fb"

# Callback types
OnOffCallback = Callable[[bool], Any]
VendorCallback = Callable[[int, bytes], Any]
CompositionCallback = Callable[[CompositionData], Any]
DisconnectCallback = Callable[[], Any]

# BlueZ D-Bus cache settle delay after device removal (seconds)
_BLUEZ_CACHE_CLEAR_DELAY = 2.0


@dataclass
class _ReassemblyBuffer:
    """Buffer for collecting segmented transport PDU chunks."""

    src: int
    dst: int
    akf: int
    aid: int
    szmic: int
    seq_zero: int
    seg_n: int
    segments: dict[int, bytes] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)


class SeqStore(Protocol):
    """Protocol for sequence number persistence.

    Implementations can persist seq to disk (HA coordinator) or keep in-memory (default).
    """

    def get_seq(self) -> int:
        """Return the current sequence number.

        Returns:
            Current sequence number.
        """
        ...

    def set_seq(self, seq: int) -> None:
        """Set the sequence number.

        Args:
            seq: Sequence number to set.
        """
        ...


class InMemorySeqStore:
    """Default in-memory sequence number store.

    Starts from 0 (not the legacy _INITIAL_SEQ=2000).
    HA coordinator should provide a persistent store via the Store mechanism.
    """

    def __init__(self, initial_seq: int = 0) -> None:
        """Initialize the in-memory seq store.

        Args:
            initial_seq: Initial sequence number (default 0).
        """
        self._seq = initial_seq

    def get_seq(self) -> int:
        """Return the current sequence number.

        Returns:
            Current sequence number.
        """
        return self._seq

    def set_seq(self, seq: int) -> None:
        """Set the sequence number.

        Args:
            seq: Sequence number to set.
        """
        self._seq = seq


class SIGMeshDevice(SIGMeshDeviceCommandsMixin, SIGMeshDeviceNotifyMixin):
    """High-level interface to a SIG Mesh device via GATT Proxy.

    Provides the same duck-type interface as ``MeshDevice`` for use
    with ``TuyaBLEMeshCoordinator``.
    """

    def __init__(
        self,
        address: str,
        target_addr: int,
        our_addr: int,
        secrets: Any,
        *,
        op_item_prefix: str = "s17",
        iv_index: int = 0,
        seq_store: SeqStore | None = None,
        ble_device_callback: Any = None,
        adapter: str | None = None,
    ) -> None:
        """Initialize a SIG Mesh device interface.

        Args:
            address: BLE MAC address (e.g. ``DC:23:4D:21:43:A5``).
            target_addr: Target unicast address (e.g. 0x00AA).
            our_addr: Our unicast address (e.g. 0x0001).
            secrets: SecretsManager instance for key loading.
            op_item_prefix: 1Password item name prefix for keys.
            iv_index: Mesh IV Index.
            seq_store: Optional SeqStore for sequence number persistence.
                If None, uses InMemorySeqStore starting from 0.
            ble_device_callback: Optional callback(address) → BLEDevice for
                HA Bluetooth Proxy support. If None, uses BleakScanner.
            adapter: BLE adapter name (e.g. "hci0"). Forces scan and connect
                via this specific adapter, bypassing HA's habluetooth routing.
        """
        self._address = address.upper()
        self._target_addr = target_addr
        self._our_addr = our_addr
        self._secrets = secrets
        self._op_item_prefix = op_item_prefix
        self._iv_index = iv_index
        self._ble_device_callback = ble_device_callback
        self._adapter = adapter

        self._client: BleakClient | None = None
        self._keys: MeshKeys | None = None
        self._seq_store: SeqStore = seq_store if seq_store is not None else InMemorySeqStore()
        self._seq_lock = asyncio.Lock()
        self._segment_lock = asyncio.Lock()  # CF-1: Protect _segment_buffers and _pending_responses
        self._tid = 0
        self._correlation_id = 0

        self._onoff_callbacks: list[OnOffCallback] = []
        self._vendor_callbacks: list[VendorCallback] = []
        self._composition_callbacks: list[CompositionCallback] = []
        self._disconnect_callbacks: list[DisconnectCallback] = []

        # Composition Data and firmware version
        self._composition: CompositionData | None = None
        self._firmware_version: str | None = None

        # Segmented message reassembly buffers: (src, dst, seq_zero, aid) -> buffer
        # Per BT Mesh spec, must include dst and aid to avoid collision
        self._segment_buffers: dict[tuple[int, int, int, int], _ReassemblyBuffer] = {}

        # Pending response futures: (opcode, correlation_id) -> Future(params)
        # Correlation ID prevents concurrent requests with same opcode from colliding
        self._pending_responses: dict[tuple[int, int], asyncio.Future[bytes]] = {}

        # Pending notify processing tasks (for lifecycle management)
        self._pending_notify_tasks: set[asyncio.Task[None]] = set()

    @property
    def address(self) -> str:
        """Return the device BLE MAC address."""
        return self._address

    @property
    def is_connected(self) -> bool:
        """Return True if the BLE client is connected."""
        return self._client is not None and self._client.is_connected

    @property
    def firmware_version(self) -> str | None:
        """Return firmware version derived from Composition Data (CID/PID/VID)."""
        return self._firmware_version

    def set_seq(self, seq: int) -> None:
        """Override the current sequence number (for restore on startup).

        Delegates to the configured seq_store.

        Args:
            seq: Sequence number to set.
        """
        self._seq_store.set_seq(seq)

    def get_seq(self) -> int:
        """Return the current sequence number (for persistence).

        Delegates to the configured seq_store.

        Returns:
            Current sequence number.
        """
        return self._seq_store.get_seq()

    def register_onoff_callback(self, callback: OnOffCallback) -> None:
        """Register a callback for GenericOnOff Status notifications.

        Args:
            callback: Called with ``on: bool`` when status received.
        """
        self._onoff_callbacks.append(callback)

    def unregister_onoff_callback(self, callback: OnOffCallback) -> None:
        """Remove a previously registered onoff callback.

        Args:
            callback: The callback to remove.
        """
        self._onoff_callbacks.remove(callback)

    def register_vendor_callback(self, callback: VendorCallback) -> None:
        """Register a callback for Tuya vendor messages.

        Args:
            callback: Called with ``(opcode: int, params: bytes)``.
        """
        self._vendor_callbacks.append(callback)

    def unregister_vendor_callback(self, callback: VendorCallback) -> None:
        """Remove a previously registered vendor callback.

        Args:
            callback: The callback to remove.
        """
        self._vendor_callbacks.remove(callback)

    def register_composition_callback(self, callback: CompositionCallback) -> None:
        """Register a callback for Composition Data responses.

        Args:
            callback: Called with ``CompositionData`` when received.
        """
        self._composition_callbacks.append(callback)

    def unregister_composition_callback(self, callback: CompositionCallback) -> None:
        """Remove a previously registered composition callback.

        Args:
            callback: The callback to remove.
        """
        self._composition_callbacks.remove(callback)

    def register_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Register a callback for disconnect events.

        Args:
            callback: Called when BLE connection is lost.
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
        timeout: float = DEFAULT_CONNECTION_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        """Connect to the device, load keys, and subscribe to notifications.

        Args:
            timeout: Connection timeout per attempt in seconds.
            max_retries: Maximum number of connection attempts.

        Raises:
            SIGMeshKeyError: If keys cannot be loaded from 1Password.
            ConnectionError: If BLE connection fails after all retries.
        """
        async with mesh_operation(self._address, "connect"):
            await self._connect_impl(timeout=timeout, max_retries=max_retries)

    async def _connect_impl(
        self,
        timeout: float = DEFAULT_CONNECTION_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        """Internal connect implementation (called within mesh_operation context).

        Args:
            timeout: Connection timeout per attempt in seconds.
            max_retries: Maximum number of connection attempts.

        Raises:
            SIGMeshKeyError: If keys cannot be loaded.
            ConnectionError: If BLE connection fails after all retries.
        """
        await self._load_keys()

        last_error: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                _LOGGER.info(
                    "Connecting to %s (attempt %d/%d)",
                    self._address,
                    attempt,
                    max_retries,
                )
                if self._ble_device_callback is not None:
                    device = self._ble_device_callback(self._address)
                else:
                    scan_kwargs: dict[str, Any] = {"timeout": timeout}
                    if self._adapter is not None:
                        scan_kwargs["adapter"] = self._adapter
                    _LOGGER.debug(
                        "Scanning for %s (adapter=%s)",
                        self._address,
                        self._adapter or "default",
                    )
                    device = await BleakScanner.find_device_by_address(self._address, **scan_kwargs)
                if device is None:
                    msg = f"Device {self._address} not found"
                    raise MeshConnectionError(msg)

                client_kwargs: dict[str, Any] = {
                    "timeout": timeout,
                    "disconnected_callback": self._on_ble_disconnect,
                }
                if self._adapter is not None:
                    client_kwargs["adapter"] = self._adapter
                client = BleakClient(device, **client_kwargs)
                await client.connect()

                # Subscribe to Proxy Data Out notifications
                # Wrap in try/except: on some BlueZ versions, start_notify
                # triggers EOFError or BleakDBusError on the D-Bus connection for mesh devices.
                try:
                    await client.start_notify(SIG_MESH_PROXY_DATA_OUT, self._on_notify)
                except (EOFError, BleakError, BleakDBusError, OSError) as notify_exc:
                    _LOGGER.warning(
                        "Notification subscription failed for %s: %s (%s) — "
                        "device will work but won't receive push status updates",
                        self._address,
                        notify_exc,
                        type(notify_exc).__name__,
                    )

                self._client = client
                _LOGGER.info("Connected to %s", self._address)

                # Request Composition Data (non-critical)
                try:
                    await self.request_composition_data()
                except (TimeoutError, SIGMeshError, BleakError):
                    _LOGGER.debug(
                        "Composition Data request failed (non-critical)",
                        exc_info=True,
                    )
                return

            except (BleakError, MeshConnectionError, OSError) as exc:
                last_error = exc
                _LOGGER.warning(
                    "Connection attempt %d failed for %s",
                    attempt,
                    self._address,
                    exc_info=True,
                )
                # Remove cached BLE device between retries
                await self._bluetoothctl_remove()
                await asyncio.sleep(_BLUEZ_CACHE_CLEAR_DELAY)

        msg = f"Failed to connect to {self._address} after {max_retries} attempts"
        raise MeshConnectionError(msg) from last_error

    async def disconnect(self) -> None:
        """Disconnect from the device and zero key material."""
        if self._client is not None:
            # HF-1: Suppress only expected BLE exceptions, not all exceptions
            with contextlib.suppress(BleakError, OSError):
                await self._client.stop_notify(SIG_MESH_PROXY_DATA_OUT)
            with contextlib.suppress(BleakError, OSError):
                await self._client.disconnect()
            self._client = None

        # Zero-fill key material before clearing (defense in depth)
        if self._keys is not None:
            # Attempt to overwrite key bytes in memory with zeros
            # Note: Python's memory management may not guarantee immediate zeroing,
            # but this is best-effort defense-in-depth against memory forensics.
            try:
                for attr in ("net_key", "dev_key", "app_key", "enc_key", "priv_key", "network_id"):
                    val = getattr(self._keys, attr, None)
                    # Overwrite mutable bytearray if possible
                    if isinstance(val, bytearray) and len(val) > 0:
                        val[:] = b"\x00" * len(val)
            except (AttributeError, TypeError):
                pass  # Frozen dataclass, best effort only
            self._keys = None

        # Cancel all pending notify tasks
        for task in self._pending_notify_tasks:
            task.cancel()
        if self._pending_notify_tasks:
            await asyncio.gather(*self._pending_notify_tasks, return_exceptions=True)
        self._pending_notify_tasks.clear()

        _LOGGER.info("Disconnected from %s", self._address)

    def _log_notify_exception(self, task: asyncio.Task[None]) -> None:
        """Log exceptions from notify processing tasks.

        Args:
            task: The completed task to check for exceptions.
        """
        if task.cancelled():
            return
        try:
            exc = task.exception()
            if exc is not None:
                _LOGGER.error(
                    "Notify processing task failed for %s",
                    self._address,
                    exc_info=exc,
                )
        except asyncio.CancelledError:
            pass
