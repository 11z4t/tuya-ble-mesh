"""High-level SIG Mesh device interface for GATT Proxy control.

Provides ``SIGMeshDevice`` for connecting to a standard Bluetooth SIG Mesh
device via GATT Proxy (UUID 0x1828), sending GenericOnOff commands, and
receiving status notifications.

Key material is loaded from 1Password via ``SecretsManager`` (Rule S10).
All byte parsing is delegated to ``sig_mesh_protocol`` (Rule S3).
All crypto operations are in ``sig_mesh_crypto`` (Rule S4).

SECURITY: Key material is NEVER logged, printed, or included in exceptions.
Only addresses, lengths, and opcodes are safe to log.

Command methods are in ``sig_mesh_device_commands``.
Segment reassembly and dispatch are in ``sig_mesh_device_segments``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
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
    SecretAccessError,
    SIGMeshError,
    SIGMeshKeyError,
)
from tuya_ble_mesh.logging_context import MeshLogAdapter, mesh_operation
from tuya_ble_mesh.sig_mesh_device_commands import SIGMeshDeviceCommandsMixin
from tuya_ble_mesh.sig_mesh_device_segments import (
    _REASSEMBLY_TIMEOUT,
    SIGMeshDeviceSegmentsMixin,
    _ReassemblyBuffer,
)
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

# Bluetoothctl remove command timeout (seconds)
_BLUETOOTHCTL_REMOVE_TIMEOUT = 5.0

# Re-export for backward compatibility (tests import from here)
__all__ = [
    "_REASSEMBLY_TIMEOUT",
    "SIGMeshDevice",
]


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


class SIGMeshDevice(SIGMeshDeviceCommandsMixin, SIGMeshDeviceSegmentsMixin):
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
            ble_device_callback: Optional callback(address) -> BLEDevice for
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

    @property
    def rssi(self) -> int | None:
        """Return the current RSSI from the BLE connection, or None if not connected.

        RSSI (Received Signal Strength Indicator) is provided by the BleakClient
        and represents the signal strength in dBm.

        Returns:
            int | None: RSSI in dBm, or None if not connected or unavailable.
        """
        if self._client is None:
            return None
        return getattr(self._client, "rssi", None)

    def set_seq(self, seq: int) -> None:
        """Override the current sequence number (for restore on startup).

        Args:
            seq: Sequence number to set.
        """
        self._seq_store.set_seq(seq)

    def get_seq(self) -> int:
        """Return the current sequence number (for persistence).

        Returns:
            Current sequence number.
        """
        return self._seq_store.get_seq()

    def register_onoff_callback(self, callback: OnOffCallback) -> None:
        """Register a callback for GenericOnOff Status notifications."""
        self._onoff_callbacks.append(callback)

    def unregister_onoff_callback(self, callback: OnOffCallback) -> None:
        """Remove a previously registered onoff callback."""
        self._onoff_callbacks.remove(callback)

    def register_vendor_callback(self, callback: VendorCallback) -> None:
        """Register a callback for Tuya vendor messages."""
        self._vendor_callbacks.append(callback)

    def unregister_vendor_callback(self, callback: VendorCallback) -> None:
        """Remove a previously registered vendor callback."""
        self._vendor_callbacks.remove(callback)

    def register_composition_callback(self, callback: CompositionCallback) -> None:
        """Register a callback for Composition Data responses."""
        self._composition_callbacks.append(callback)

    def unregister_composition_callback(self, callback: CompositionCallback) -> None:
        """Remove a previously registered composition callback."""
        self._composition_callbacks.remove(callback)

    def register_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Register a callback for disconnect events."""
        self._disconnect_callbacks.append(callback)

    def unregister_disconnect_callback(self, callback: DisconnectCallback) -> None:
        """Remove a previously registered disconnect callback."""
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
                        device = await BleakScanner.find_device_by_address(
                            self._address, **scan_kwargs
                        )
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
            try:
                for attr in ("net_key", "dev_key", "app_key", "enc_key", "priv_key", "network_id"):
                    val = getattr(self._keys, attr, None)
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

    # --- Private helpers ---

    async def _next_seq(self) -> int:
        """Return and increment the sequence number (24-bit wrap).

        Protected by asyncio.Lock to prevent nonce collision from
        concurrent callers. Delegates to seq_store.

        Raises:
            SIGMeshError: If sequence number exhausted (> 0xFFFFFF).
        """
        async with self._seq_lock:
            seq = self._seq_store.get_seq()
            if seq > 0xFFFFFF:
                msg = "Sequence number exhausted — reconnect required"
                raise SIGMeshError(msg)
            self._seq_store.set_seq(seq + 1)
            return seq

    async def _next_seqs(self, n: int) -> int:
        """Reserve n consecutive sequence numbers and return the first.

        Used for segmented messages that need a contiguous seq range.
        Wraps at 24-bit boundary per SIG Mesh spec. Delegates to seq_store.

        Args:
            n: Number of sequence numbers to reserve.

        Returns:
            First sequence number of the reserved range.

        Raises:
            SIGMeshError: If sequence number exhausted (> 0xFFFFFF).
        """
        async with self._seq_lock:
            seq = self._seq_store.get_seq()
            if seq > 0xFFFFFF or (seq + n) > 0xFFFFFF:
                msg = "Sequence number exhausted — reconnect required"
                raise SIGMeshError(msg)
            self._seq_store.set_seq(seq + n)
            return seq

    async def _load_keys(self) -> None:
        """Load mesh keys from 1Password via SecretsManager.

        Raises:
            SIGMeshKeyError: If any required key is missing.
        """
        prefix = self._op_item_prefix
        try:
            net_key_hex = await self._secrets.get(
                f"{prefix}-net-key",
                "password",  # pragma: allowlist secret
            )
            dev_key_hex = await self._secrets.get(
                f"{prefix}-dev-key-{self._target_addr:04x}",
                "password",  # pragma: allowlist secret
            )
            app_key_hex = await self._secrets.get(
                f"{prefix}-app-key",
                "password",  # pragma: allowlist secret
            )
        except (SecretAccessError, OSError, ValueError, RuntimeError) as exc:
            msg = f"Failed to load SIG Mesh keys for prefix '{prefix}'"
            raise SIGMeshKeyError(msg) from exc

        self._keys = MeshKeys(
            net_key_hex,
            dev_key_hex,
            app_key_hex,
            iv_index=self._iv_index,
        )
        _LOGGER.info(
            "SIG Mesh keys loaded (prefix=%s, NID=0x%02X, AID=0x%02X)",
            prefix,
            self._keys.nid,
            self._keys.aid,
        )

    async def _bluetoothctl_remove(self) -> None:
        """Remove device from BlueZ cache via bluetoothctl."""
        try:
            process = await asyncio.create_subprocess_exec(
                "bluetoothctl",
                "remove",
                self._address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(process.wait(), timeout=_BLUETOOTHCTL_REMOVE_TIMEOUT)
        except (TimeoutError, OSError):
            _LOGGER.debug("bluetoothctl remove failed (ignored)", exc_info=True)
