"""Push-based coordinator for Tuya BLE Mesh devices.

Subclasses DataUpdateCoordinator with update_interval=None (push-based).
BLE notifications drive state updates via async_set_updated_data().
Reconnection uses exponential backoff, triggered by disconnect callbacks.

Error classification:
  bridge_down      — HTTP bridge not reachable
  device_offline   — Bridge up but device MAC not responding
  mesh_auth        — Device found but credentials rejected
  protocol         — Protocol negotiation / version mismatch
  permanent        — Fatal error, no reconnect useful (e.g. unsupported device)
  transient        — Temporary failure (timeout, flap), reconnect will fix
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import statistics
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Union

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from tuya_ble_mesh.exceptions import (
    AuthenticationError,
    CryptoError,
    DeviceNotFoundError,
    MeshConnectionError,
    MeshTimeoutError,
    ProtocolError,
    SIGMeshKeyError,
)

from custom_components.tuya_ble_mesh.device_capabilities import DeviceCapabilities

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store
    from tuya_ble_mesh.device import MeshDevice
    from tuya_ble_mesh.protocol import StatusResponse
    from tuya_ble_mesh.sig_mesh_bridge import (
        SIGMeshBridgeDevice,
        TelinkBridgeDevice,
    )
    from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice
    from tuya_ble_mesh.sig_mesh_protocol import CompositionData
# All supported device types that can be passed to the coordinator
AnyMeshDevice = Union[
    "MeshDevice",
    "SIGMeshDevice",
    "TelinkBridgeDevice",
    "SIGMeshBridgeDevice",
]

# RSSI refresh interval (seconds) - adaptive
_RSSI_MIN_INTERVAL = 30.0  # Minimum when values change frequently
_RSSI_MAX_INTERVAL = 300.0  # Maximum when stable
_RSSI_DEFAULT_INTERVAL = 60.0  # Initial/fallback
_RSSI_STABILITY_THRESHOLD = 3  # No changes for N cycles = stable

_LOGGER = logging.getLogger(__name__)

# Reconnect backoff parameters
_INITIAL_BACKOFF = 5.0
_MAX_BACKOFF = 300.0
_BACKOFF_MULTIPLIER = 2.0

# Bridge-specific reconnect parameters (shorter backoff for HTTP bridges)
_BRIDGE_INITIAL_BACKOFF = 3.0
_BRIDGE_MAX_BACKOFF = 120.0

# Reconnect storm detection: threshold reconnects within window (seconds)
_STORM_WINDOW_SECONDS = 300  # 5 minutes
_STORM_DEFAULT_THRESHOLD = 10

# Max consecutive reconnect failures before giving up (0 = unlimited)
_DEFAULT_MAX_RECONNECT_FAILURES = 0

# Listener error tolerance: remove broken callbacks after this many consecutive failures
_MAX_CALLBACK_ERRORS = 3

# Rate limiting: cap concurrent BLE commands to avoid mesh saturation
_COMMAND_CONCURRENCY_LIMIT = 5


@dataclass
class ReconnectEvent:
    """A single reconnect attempt record for timeline diagnostics.

    Stored in ConnectionStatistics.reconnect_timeline (last 20 events).
    Used by diagnostics to explain *when* and *why* the device went offline.
    """

    timestamp: float  # Unix time of the attempt
    error_class: str  # ErrorClass.value (e.g. "transient", "mesh_auth")
    backoff: float  # Backoff delay (seconds) applied *before* this attempt
    attempt: int  # Consecutive failure count at time of this event


_RECONNECT_TIMELINE_MAX = 20  # Maximum reconnect events retained in timeline


class ErrorClass(StrEnum):
    """Classification of connection/protocol errors for repair creation."""

    BRIDGE_DOWN = "bridge_down"
    DEVICE_OFFLINE = "device_offline"
    MESH_AUTH = "mesh_auth"
    PROTOCOL = "protocol"
    PERMANENT = "permanent"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"


class StateUpdateSource(StrEnum):
    """Source of a device state update for confidence tracking."""

    NOTIFY = "notify"  # BLE notification from device (confirmed, highest confidence)
    POLL = "poll"  # Polled status response (confirmed, high confidence)
    COMMAND_ECHO = "command_echo"  # Device echoed our command (confirmed, medium confidence)
    ASSUMED = "assumed"  # Optimistic assumption after write (unconfirmed, low confidence)


class DeviceAvailabilityState(StrEnum):
    """Per-device availability state (PLAT-402 Phase 1 Task 1.3).

    More granular than the binary available flag. Tracks the reason
    why a device might be unavailable or degraded.
    """

    UNKNOWN = "unknown"  # Initial state before first contact
    AVAILABLE = "available"  # Device online and responding normally
    STALE = "stale"  # No recent updates but not yet timed out
    ASSUMED_ONLINE = "assumed_online"  # Optimistically assumed (command sent, no confirm)
    UNREACHABLE = "unreachable"  # Timeout or connection failed
    REPROVISION_REQUIRED = "reprovision_required"  # Auth failed, needs re-provisioning


# Sequence number persistence
_SEQ_PERSIST_INTERVAL = 10  # Save seq every N commands
_SEQ_SAFETY_MARGIN = 100  # Add margin on restore to avoid replay
_SEQ_STORE_VERSION = 1


@dataclass(frozen=True, slots=True)
class TuyaBLEMeshDeviceState:
    """Immutable snapshot of a Tuya BLE Mesh device state.

    All updates must use dataclasses.replace() to produce a new snapshot.
    This guarantees atomic state transitions with no partially-updated views.

    Task 1.2 (PLAT-402 Phase 1): Desired vs Confirmed State Model
    --------------------------------------------------------------
    desired_state: What the user/automation wants (last command sent)
    last_sent_state: What we actually sent to the device (may differ due to clamping/rounding)
    last_confirmed_state: Last state the device confirmed via notification/poll
    state_confidence: 0.0-1.0 — how confident we are in current state
    last_update_source: Where the current state came from (notify/poll/assumed)
    last_update_time: When the current state was updated
    """

    is_on: bool = False
    brightness: int = 0
    color_temp: int = 0
    mode: int = 0
    red: int = 0
    green: int = 0
    blue: int = 0
    color_brightness: int = 0
    rssi: int | None = None
    firmware_version: str | None = None
    power_w: float | None = None
    energy_kwh: float | None = None
    available: bool = False
    scene_id: int = 0  # Active scene/effect index (0 = none)
    last_seen: float | None = None  # Unix timestamp of last successful communication

    # --- PLAT-402 Phase 1 Task 1.2: Desired vs Confirmed State ---
    desired_state: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    last_sent_state: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    last_confirmed_state: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    state_confidence: float = 0.0  # 0.0 = no confidence, 1.0 = fully confirmed
    last_update_source: str = StateUpdateSource.ASSUMED.value
    last_update_time: float | None = None  # Unix timestamp of last state update

    # --- PLAT-402 Phase 1 Task 1.3: Extended Connection State ---
    device_availability: str = DeviceAvailabilityState.UNKNOWN.value  # Granular availability state
    consecutive_write_failures: int = 0  # Triggers DEGRADED at threshold
    degraded_reason: str | None = None  # Human-readable reason for DEGRADED/UNREACHABLE


@dataclass
class ConnectionStatistics:
    """Connection and performance statistics for diagnostics."""

    connect_time: float | None = None  # Unix timestamp of last successful connect
    total_reconnects: int = 0
    total_errors: int = 0
    connection_errors: int = 0
    command_errors: int = 0
    response_times: deque[float] = field(default_factory=lambda: deque(maxlen=100))
    last_error: str | None = None
    last_error_time: float | None = None
    last_error_class: str = ErrorClass.UNKNOWN.value
    connection_uptime: float = 0.0  # Total seconds connected
    last_disconnect_time: float | None = None
    avg_response_time: float = 0.0  # Average response time in seconds
    # Reconnect storm tracking
    reconnect_times: deque[float] = field(default_factory=lambda: deque(maxlen=50))
    storm_detected: bool = False
    # Reconnect timeline — last N events with error class and backoff.
    # deque(maxlen) automatically evicts the oldest entry when full — O(1) append,
    # no manual pop(0) needed (which would be O(n) on a plain list).
    reconnect_timeline: deque[ReconnectEvent] = field(
        default_factory=lambda: deque(maxlen=_RECONNECT_TIMELINE_MAX)
    )
    # RSSI history — (timestamp, dBm) tuples for trend analysis
    rssi_history: deque[tuple[float, int]] = field(default_factory=lambda: deque(maxlen=50))


class TuyaBLEMeshCoordinator(DataUpdateCoordinator[None]):
    """Push-based coordinator for a single BLE mesh device.

    Subclasses DataUpdateCoordinator with update_interval=None.
    State updates arrive via BLE notifications and are dispatched
    via async_set_updated_data(None). CoordinatorEntity subclasses
    get automatic async_write_ha_state() calls.
    """

    def __init__(
        self,
        device: AnyMeshDevice,
        *,
        hass: HomeAssistant | None = None,
        entry_id: str | None = None,
    ) -> None:
        if hass is not None:
            super().__init__(
                hass,
                _LOGGER,
                name=f"tuya_ble_mesh_{device.address}",
                update_interval=None,  # Push-based, no polling
            )
        self._device: AnyMeshDevice = device
        self.capabilities = DeviceCapabilities.from_device(device)
        self._state = TuyaBLEMeshDeviceState()
        self._reconnect_task: asyncio.Task[None] | None = None
        self._rssi_task: asyncio.Task[None] | None = None
        self._backoff = _INITIAL_BACKOFF
        self._running = False

        # Sequence number persistence (SIG Mesh only)
        self._hass = hass
        self._entry_id = entry_id
        self._seq_store: Store[dict[str, int]] | None = None
        self._seq_command_count = 0
        self._seq_persist_task: asyncio.Task[None] | None = None

        # Adaptive polling - track state change frequency
        self._rssi_interval = _RSSI_DEFAULT_INTERVAL
        self._state_change_counter = 0
        self._stable_cycles = 0

        # Connection statistics for diagnostics
        self._stats = ConnectionStatistics()

        # Entry name for repair issue placeholders (set externally if needed)
        self.entry_name: str = ""
        self._storm_threshold: int = _STORM_DEFAULT_THRESHOLD

        # Consecutive failure tracking for max reconnect limit
        self._max_reconnect_failures: int = _DEFAULT_MAX_RECONNECT_FAILURES
        self._consecutive_failures: int = 0

        # Track which repair issues have already been raised (cleared on recovery)
        self._raised_repair_issues: set[str] = set()

        # Standalone listener support (test / non-HA mode, hass=None)
        # NOTE: Do NOT touch self._listeners — the parent
        # DataUpdateCoordinator.__init__() manages it for HA's listener system.
        self._standalone_listeners: list[Callable[[], None]] = []
        self._listener_error_counts: dict[int, int] = {}

        # Rate limiting: semaphore caps concurrent BLE commands per device
        self._command_semaphore = asyncio.Semaphore(_COMMAND_CONCURRENCY_LIMIT)

    async def _async_update_data(self) -> None:
        """No-op — state updates arrive via BLE notifications."""
        return None

    # --- Public read-only properties ---

    @property
    def consecutive_failures(self) -> int:
        """Number of consecutive reconnect failures since last successful connect.

        Read-only; mutated internally by the reconnect loop. Exposed as a
        property so external modules (e.g. diagnostics) do not need to access
        the private ``_consecutive_failures`` attribute.
        """
        return self._consecutive_failures

    @property
    def storm_threshold(self) -> int:
        """Reconnect-storm detection threshold (number of reconnects per 5-min window).

        Read-only; set from options at coordinator creation time. Exposed as a
        property so diagnostics and tests can read it without private-attribute access.
        """
        return self._storm_threshold

    # --- Listener support ---
    # In HA context, listeners are managed by DataUpdateCoordinator.async_add_listener()
    # and dispatched via async_set_updated_data().
    # In standalone/test mode (hass=None), a simple list-based listener system is used.

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a state change listener.

        In standalone mode (hass=None): stores listener in _listeners list.
        With hass: raises RuntimeError (use async_add_listener instead).

        Returns a callback to remove the listener.
        """
        self._standalone_listeners.append(listener)

        def _remove() -> None:
            with contextlib.suppress(ValueError):
                self._standalone_listeners.remove(listener)
            self._listener_error_counts.pop(id(listener), None)

        return _remove

    def _notify_listeners(self) -> None:
        """Notify standalone listeners (used in test mode when hass=None)."""
        listeners = list(self._standalone_listeners)
        _LOGGER.debug("Notifying %d listener(s)", len(listeners))
        for listener in listeners:
            try:
                listener()
                self._listener_error_counts.pop(id(listener), None)
            except Exception:
                cb_id = id(listener)
                count = self._listener_error_counts.get(cb_id, 0) + 1
                self._listener_error_counts[cb_id] = count
                if count >= _MAX_CALLBACK_ERRORS:
                    with contextlib.suppress(ValueError, AttributeError):
                        self._standalone_listeners.remove(listener)
                    self._listener_error_counts.pop(cb_id, None)

    def _dispatch_update(self) -> None:
        """Thread-safe wrapper to dispatch state updates.

        BLE notification callbacks (Bleak) may fire on a background thread.
        In HA context, uses call_soon_threadsafe to ensure the update is
        dispatched on the HA event loop. In standalone/test mode (hass=None),
        notifies any listeners registered via add_listener() directly.
        """
        if self._hass is not None:
            self._hass.loop.call_soon_threadsafe(self.async_set_updated_data, None)
        else:
            # Standalone / test — notify registered listeners directly
            self._notify_listeners()

    @property
    def device(self) -> AnyMeshDevice:
        """Return the underlying mesh device."""
        return self._device

    @property
    def state(self) -> TuyaBLEMeshDeviceState:
        """Return the current device state."""
        return self._state

    @property
    def statistics(self) -> ConnectionStatistics:
        """Return connection and performance statistics."""
        return self._stats

    @property
    def avg_response_time_ms(self) -> float | None:
        """Return mean connection response time in milliseconds, or None if no data."""
        if not self._stats.response_times:
            return None
        return statistics.mean(self._stats.response_times) * 1000

    def _log_connect_metrics(self, response_time: float) -> None:
        """Log connection performance metrics at INFO level.

        Logs response time and rolling average to help diagnose slow adapters.

        Args:
            response_time: Connect time in seconds for this attempt.
        """
        avg_ms = self.avg_response_time_ms
        if avg_ms is not None:
            _LOGGER.info(
                "Connect metrics for %s: this=%.0fms avg=%.0fms reconnects=%d errors=%d",
                self._device.address,
                response_time * 1000,
                avg_ms,
                self._stats.total_reconnects,
                self._stats.total_errors,
            )
        else:
            _LOGGER.info(
                "Connect metrics for %s: this=%.0fms (first connection)",
                self._device.address,
                response_time * 1000,
            )

    # Listeners are managed by DataUpdateCoordinator.async_add_listener()
    # and dispatched automatically when async_set_updated_data() is called.

    def _on_onoff_update(self, on: bool) -> None:
        """Handle a GenericOnOff Status from a SIG Mesh device.

        Args:
            on: True if device is on, False if off.
        """
        was_available = self._state.available
        changed = self._state.is_on != on
        now = time.time()

        # PLAT-402 Task 1.2: Confirmed state from device notification
        confirmed = MappingProxyType({"is_on": on})
        # PLAT-402 Task 1.3: Reset failure counters on successful notify
        self._state = replace(
            self._state,
            is_on=on,
            available=True,
            last_seen=now,
            last_confirmed_state=confirmed,
            state_confidence=1.0,
            last_update_source=StateUpdateSource.NOTIFY.value,
            last_update_time=now,
            device_availability=DeviceAvailabilityState.AVAILABLE.value,
            consecutive_write_failures=0,
            degraded_reason=None,
        )
        self._backoff = _INITIAL_BACKOFF

        # Adaptive polling: track state changes
        if changed:
            self._state_change_counter += 1
            self._stable_cycles = 0
            self._adjust_polling_interval()

        # Periodic seq persistence
        self._seq_command_count += 1
        if self._seq_command_count >= _SEQ_PERSIST_INTERVAL:
            self._seq_command_count = 0
            try:
                asyncio.get_running_loop()
                self._seq_persist_task = asyncio.create_task(self._save_seq())
            except RuntimeError:
                pass  # No event loop in standalone/test context

        _LOGGER.debug(
            "OnOff update: on=%s (changed=%s, source=%s, confidence=%.2f)",
            on,
            changed,
            StateUpdateSource.NOTIFY.value,
            1.0,
        )
        # Only notify if state changed or device just became available (avoids
        # spurious HA entity writes when repeated identical status messages arrive)
        if changed or not was_available:
            self._dispatch_update()

    def _on_status_update(self, status: StatusResponse) -> None:
        """Handle a status notification from the device.

        Args:
            status: Decoded status from BLE notification.
        """
        was_available = self._state.available
        now = time.time()

        # Detect changes for adaptive polling
        changed = (
            self._state.mode != status.mode
            or self._state.brightness != status.white_brightness
            or self._state.color_temp != status.white_temp
            or self._state.red != status.red
            or self._state.green != status.green
            or self._state.blue != status.blue
            or self._state.color_brightness != status.color_brightness
        )

        is_on = status.white_brightness > 0 or status.color_brightness > 0

        # PLAT-402 Task 1.2: Confirmed state from device notification
        confirmed = MappingProxyType({
            "is_on": is_on,
            "mode": status.mode,
            "brightness": status.white_brightness,
            "color_temp": status.white_temp,
            "red": status.red,
            "green": status.green,
            "blue": status.blue,
            "color_brightness": status.color_brightness,
        })

        # PLAT-402 Task 1.3: Reset failure counters on successful notify
        self._state = replace(
            self._state,
            mode=status.mode,
            brightness=status.white_brightness,
            color_temp=status.white_temp,
            red=status.red,
            green=status.green,
            blue=status.blue,
            color_brightness=status.color_brightness,
            is_on=is_on,
            available=True,
            last_confirmed_state=confirmed,
            state_confidence=1.0,
            last_update_source=StateUpdateSource.NOTIFY.value,
            last_update_time=now,
            device_availability=DeviceAvailabilityState.AVAILABLE.value,
            consecutive_write_failures=0,
            degraded_reason=None,
        )
        self._backoff = _INITIAL_BACKOFF

        # Adaptive polling: track state changes
        if changed:
            self._state_change_counter += 1
            self._stable_cycles = 0
            self._adjust_polling_interval()

        _LOGGER.debug(
            "Status update: on=%s mode=%d bright=%d temp=%d rgb=(%d,%d,%d) "
            "cbright=%d (changed=%s, source=%s, confidence=%.2f)",
            self._state.is_on,
            self._state.mode,
            self._state.brightness,
            self._state.color_temp,
            self._state.red,
            self._state.green,
            self._state.blue,
            self._state.color_brightness,
            changed,
            StateUpdateSource.NOTIFY.value,
            1.0,
        )

        # Only notify if state changed or device just became available (avoids
        # spurious HA entity writes when repeated identical status messages arrive)
        if changed or not was_available:
            self._dispatch_update()

    def _on_vendor_update(self, opcode: int, params: bytes) -> None:
        """Handle a Tuya vendor message from a SIG Mesh device.

        Parses vendor frames: DP data updates power/energy state,
        timestamp sync requests get an automatic response.

        Args:
            opcode: 3-byte vendor opcode.
            params: Raw vendor message parameters.
        """
        from tuya_ble_mesh.sig_mesh_protocol import (
            DP_ID_ENERGY_KWH,
            DP_ID_POWER_W,
            TUYA_CMD_TIMESTAMP_SYNC,
            TUYA_VENDOR_OPCODE,
            parse_tuya_vendor_frame,
        )

        if opcode != TUYA_VENDOR_OPCODE:
            return

        frame = parse_tuya_vendor_frame(params)

        # Respond to timestamp sync requests automatically
        if frame.command == TUYA_CMD_TIMESTAMP_SYNC:
            _LOGGER.info("Device requested timestamp sync — sending response")
            self.hass.async_create_task(self._send_timestamp_response())
            return

        # Process DP data
        power_w = self._state.power_w
        energy_kwh = self._state.energy_kwh
        updated = False
        for dp in frame.dps:
            if dp.dp_id == DP_ID_POWER_W and len(dp.value) >= 1:
                raw = int.from_bytes(dp.value, "big")
                power_w = raw / 10.0
                updated = True
                _LOGGER.debug("Power: %.1f W (raw=%d)", power_w, raw)
            elif dp.dp_id == DP_ID_ENERGY_KWH and len(dp.value) >= 1:
                raw = int.from_bytes(dp.value, "big")
                energy_kwh = raw / 100.0
                updated = True
                _LOGGER.debug("Energy: %.2f kWh (raw=%d)", energy_kwh, raw)

        if updated:
            now = time.time()
            # PLAT-402 Task 1.2: Confirmed vendor data (power/energy)
            confirmed_dict = dict(self._state.last_confirmed_state)
            if power_w is not None:
                confirmed_dict["power_w"] = power_w
            if energy_kwh is not None:
                confirmed_dict["energy_kwh"] = energy_kwh
            confirmed = MappingProxyType(confirmed_dict)

            self._state = replace(
                self._state,
                power_w=power_w,
                energy_kwh=energy_kwh,
                available=True,
                last_confirmed_state=confirmed,
                state_confidence=1.0,
                last_update_source=StateUpdateSource.NOTIFY.value,
                last_update_time=now,
            )
            self._dispatch_update()

    async def _send_timestamp_response(self) -> None:
        """Send Tuya vendor timestamp sync response to device."""
        from tuya_ble_mesh.sig_mesh_protocol import tuya_vendor_timestamp_response

        try:
            payload = tuya_vendor_timestamp_response()
            await self._device.send_vendor_command(payload)
            _LOGGER.info("Timestamp sync response sent")
        except Exception:
            _LOGGER.warning("Failed to send timestamp sync response", exc_info=True)

    def _on_composition_update(self, comp: CompositionData) -> None:
        """Handle a Composition Data response from a SIG Mesh device.

        Updates firmware_version in state from device's firmware_version property.

        Args:
            comp: Parsed composition data.
        """
        self._state = replace(self._state, firmware_version=self._device.firmware_version)
        _LOGGER.debug("Composition Data received, firmware=%s", self._state.firmware_version)
        self._dispatch_update()

    def _on_disconnect(self) -> None:
        """Handle device disconnect — mark unavailable and schedule reconnect.

        Called by MeshDevice disconnect callback when the BLE connection
        is lost (write failure or keep-alive timeout).

        For bridge devices, uses shorter backoff since HTTP reconnects
        are cheaper than BLE reconnects. Entities are marked unavailable
        immediately and will auto-recover when reconnect succeeds.
        """
        _LOGGER.warning("Device disconnected: %s", self._device.address)

        # Update connection statistics
        if self._stats.connect_time is not None:
            uptime = time.time() - self._stats.connect_time
            self._stats.connection_uptime += uptime
        self._stats.last_disconnect_time = time.time()

        # Calculate average response time for diagnostics
        if self._stats.response_times:
            self._stats.avg_response_time = sum(self._stats.response_times) / len(
                self._stats.response_times
            )

        # Mark all state as unavailable — entities will show "unavailable" in HA
        self._state = replace(self._state, available=False)
        self._stop_rssi_polling()

        # Use bridge-specific backoff if this is a bridge device
        if self._is_bridge_device():
            self._backoff = _BRIDGE_INITIAL_BACKOFF

        self._dispatch_update()
        self.schedule_reconnect()

    async def _load_seq(self) -> None:
        """Load persisted sequence number from HA Store.

        Adds a safety margin to avoid sequence number reuse after crash.
        No-op if hass or entry_id is None.
        """
        if self._hass is None or self._entry_id is None:
            return
        if not self.capabilities.has_sig_sequence:
            return

        from homeassistant.helpers.storage import Store

        self._seq_store = Store(
            self._hass,
            _SEQ_STORE_VERSION,
            f"tuya_ble_mesh.seq.{self._entry_id}",
        )

        data = await self._seq_store.async_load()
        if data is not None and "seq" in data:
            restored_seq = (data["seq"] + _SEQ_SAFETY_MARGIN) & 0xFFFFFF  # Wrap within 24-bit range
            self._device.set_seq(restored_seq)
            _LOGGER.info(
                "Restored seq=%d (stored=%d + margin=%d)",
                restored_seq,
                data["seq"],
                _SEQ_SAFETY_MARGIN,
            )

    async def _save_seq(self) -> None:
        """Persist current sequence number to HA Store.

        No-op if store is not initialized.
        """
        if self._seq_store is None or not self.capabilities.has_sig_sequence:
            return

        seq = self._device.get_seq()
        await self._seq_store.async_save({"seq": seq})
        _LOGGER.debug("Persisted seq=%d", seq)

    async def async_start(self) -> None:
        """Start the coordinator — connect and begin receiving notifications."""
        self._running = True

        # Restore sequence number before connecting
        await self._load_seq()

        # Wire callbacks based on device capabilities
        if self.capabilities.has_onoff_callback:
            self._device.register_onoff_callback(self._on_onoff_update)
        if self.capabilities.has_vendor_callback:
            self._device.register_vendor_callback(self._on_vendor_update)
        if self.capabilities.has_composition_callback:
            self._device.register_composition_callback(self._on_composition_update)
        if self.capabilities.has_status_callback:
            self._device.register_status_callback(self._on_status_update)
        self._device.register_disconnect_callback(self._on_disconnect)

        try:
            start_time = time.monotonic()
            await self._device.connect()
            response_time = time.monotonic() - start_time
            self._stats.response_times.append(response_time)
            self._stats.connect_time = time.time()
            self._state = replace(
                self._state,
                available=True,
                firmware_version=self._device.firmware_version,
                last_seen=time.time(),
            )
            self._backoff = _INITIAL_BACKOFF
            self._start_rssi_polling()
            _LOGGER.info("Coordinator started for %s (%.2fs)", self._device.address, response_time)
            self._log_connect_metrics(response_time)
        except Exception as err:
            self._stats.total_errors += 1
            self._stats.connection_errors += 1
            self._stats.last_error = str(err)
            self._stats.last_error_time = time.time()
            _LOGGER.warning(
                "Initial connection failed for %s, scheduling reconnect",
                self._device.address,
                exc_info=True,
            )
            self._state = replace(self._state, available=False)
            self.schedule_reconnect()

        self._dispatch_update()

    async def async_stop(self) -> None:
        """Stop the coordinator — disconnect and cancel reconnection.

        Cancels all background tasks and awaits their completion to prevent
        'task was destroyed but it is pending' warnings and resource leaks.
        """
        self._running = False

        # Persist seq before stopping
        await self._save_seq()

        # Cancel all background tasks and await them
        tasks_to_cancel: list[asyncio.Task[None]] = []
        if self._rssi_task is not None:
            self._rssi_task.cancel()
            tasks_to_cancel.append(self._rssi_task)
            self._rssi_task = None
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            tasks_to_cancel.append(self._reconnect_task)
            self._reconnect_task = None
        if self._seq_persist_task is not None:
            self._seq_persist_task.cancel()
            tasks_to_cancel.append(self._seq_persist_task)
            self._seq_persist_task = None

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        # Unregister callbacks (try/except in case device is already gone)
        for attr, callback in (
            ("unregister_onoff_callback", self._on_onoff_update),
            ("unregister_vendor_callback", self._on_vendor_update),
            ("unregister_composition_callback", self._on_composition_update),
            ("unregister_status_callback", self._on_status_update),
        ):
            if hasattr(self._device, attr):
                with contextlib.suppress(ValueError, AttributeError):
                    # Already unregistered or device gone
                    getattr(self._device, attr)(callback)
        with contextlib.suppress(ValueError, AttributeError):
            self._device.unregister_disconnect_callback(self._on_disconnect)

        try:
            await self._device.disconnect()
        except Exception:
            _LOGGER.debug("Disconnect error during stop (ignored)", exc_info=True)

        self._state = replace(self._state, available=False)
        _LOGGER.info("Coordinator stopped for %s", self._device.address)

    def schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt with exponential backoff."""
        if not self._running:
            return

        # No-op when running outside of HA event loop (e.g. standalone tests)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        _ = loop  # used implicitly by create_task

        if self._reconnect_task is not None:
            self._reconnect_task.cancel()

        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        self._reconnect_task.add_done_callback(self._log_task_exception)

    def _log_task_exception(self, task: asyncio.Task) -> None:
        """Log exceptions from background tasks.

        Args:
            task: The completed task to check for exceptions.
        """
        if task.cancelled():
            return
        try:
            exc = task.exception()
            if exc is not None:
                _LOGGER.error(
                    "Reconnect task failed for %s",
                    self._device_name,
                    exc_info=exc,
                )
        except Exception:
            # Task is not done or other edge cases
            pass

    def _classify_error(self, err: Exception) -> ErrorClass:
        """Classify a connection error into a category for repair creation.

        Primary path: isinstance checks against the lib exception hierarchy —
        deterministic, rename-safe, and independent of message wording.
        Fallback: string heuristics for generic OS/asyncio errors that are not
        wrapped in a lib exception (e.g. ConnectionRefusedError from aiohttp).
        """
        # --- Lib exception hierarchy (isinstance — authoritative) ---
        if isinstance(err, (AuthenticationError, CryptoError, SIGMeshKeyError)):
            return ErrorClass.MESH_AUTH
        if isinstance(err, MeshTimeoutError):
            return ErrorClass.TRANSIENT
        if isinstance(err, ProtocolError):
            return ErrorClass.PROTOCOL
        if isinstance(err, DeviceNotFoundError):
            return ErrorClass.DEVICE_OFFLINE
        if isinstance(err, MeshConnectionError):
            # Generic connection failure — could be bridge-down or BLE drop.
            # Use message heuristics to distinguish the two.
            err_msg = str(err).lower()
            if "connection refused" in err_msg or "unreachable" in err_msg or "no route" in err_msg:
                return ErrorClass.BRIDGE_DOWN
            return ErrorClass.TRANSIENT

        # --- Fallback: generic OS / asyncio / aiohttp errors ---
        err_msg = str(err).lower()
        if (
            "unsupported device" in err_msg
            or "unsupported" in err_msg
            or "unknown vendor" in err_msg
        ):
            return ErrorClass.PERMANENT
        if "timeout" in err_msg or isinstance(err, (asyncio.TimeoutError, TimeoutError)):
            return ErrorClass.TRANSIENT
        if "auth" in err_msg or "password" in err_msg or "credential" in err_msg:
            return ErrorClass.MESH_AUTH
        if "protocol" in err_msg or "version" in err_msg:
            return ErrorClass.PROTOCOL
        if "connection refused" in err_msg or "unreachable" in err_msg or "no route" in err_msg:
            return ErrorClass.BRIDGE_DOWN
        if "not found" in err_msg:
            return ErrorClass.DEVICE_OFFLINE
        return ErrorClass.UNKNOWN

    def _maybe_create_repair_issue(self, error_class: ErrorClass) -> None:
        """Create a repair issue for the given error class, at most once per recovery.

        Issues are only created when hass and entry_id are available, and only
        once per error class (tracked in _raised_repair_issues). They are cleared
        on successful reconnect via _clear_repair_issues_on_recovery().
        """
        if self._hass is None or self._entry_id is None:
            return

        from custom_components.tuya_ble_mesh.repairs import (
            ISSUE_AUTH_OR_MESH_MISMATCH,
            ISSUE_BRIDGE_UNREACHABLE,
            ISSUE_DEVICE_NOT_FOUND,
            ISSUE_TIMEOUT,
            async_create_issue_auth_or_mesh_mismatch,
            async_create_issue_bridge_unreachable,
            async_create_issue_device_not_found,
            async_create_issue_timeout,
        )

        _CLASS_TO_ISSUE = {
            ErrorClass.BRIDGE_DOWN: (ISSUE_BRIDGE_UNREACHABLE, None),
            ErrorClass.MESH_AUTH: (ISSUE_AUTH_OR_MESH_MISMATCH, None),
            ErrorClass.DEVICE_OFFLINE: (ISSUE_DEVICE_NOT_FOUND, None),
            ErrorClass.TRANSIENT: (ISSUE_TIMEOUT, None),
        }
        mapping = _CLASS_TO_ISSUE.get(error_class)
        if mapping is None:
            return

        issue_base, _ = mapping
        if issue_base in self._raised_repair_issues:
            return  # Already raised since last recovery

        self._raised_repair_issues.add(issue_base)
        name = self.entry_name or self._device.address
        host = getattr(self._device, "host", "") or ""
        port = getattr(self._device, "port", 0) or 0
        hass = self._hass
        entry_id = self._entry_id

        mac = self._device.address

        async def _create() -> None:
            if error_class == ErrorClass.BRIDGE_DOWN:
                await async_create_issue_bridge_unreachable(hass, host, port, entry_id)
            elif error_class == ErrorClass.MESH_AUTH:
                await async_create_issue_auth_or_mesh_mismatch(hass, name, entry_id)
            elif error_class == ErrorClass.DEVICE_OFFLINE:
                await async_create_issue_device_not_found(hass, name, mac, entry_id)
            elif error_class == ErrorClass.TRANSIENT:
                await async_create_issue_timeout(hass, name, entry_id)

        task = asyncio.create_task(_create())
        task.add_done_callback(self._log_task_exception)

    def _clear_repair_issues_on_recovery(self) -> None:
        """Clear all connection repair issues after successful reconnect."""
        if self._hass is None or self._entry_id is None:
            return

        from custom_components.tuya_ble_mesh.repairs import (
            ISSUE_AUTH_OR_MESH_MISMATCH,
            ISSUE_BRIDGE_UNREACHABLE,
            ISSUE_DEVICE_NOT_FOUND,
            ISSUE_RECONNECT_STORM,
            ISSUE_TIMEOUT,
            async_delete_issue,
        )

        for base_id in (
            ISSUE_BRIDGE_UNREACHABLE,
            ISSUE_AUTH_OR_MESH_MISMATCH,
            ISSUE_DEVICE_NOT_FOUND,
            ISSUE_TIMEOUT,
            ISSUE_RECONNECT_STORM,
        ):
            async_delete_issue(self._hass, base_id, self._entry_id)
        self._raised_repair_issues.clear()

    def _check_reconnect_storm(self) -> bool:
        """Return True if reconnect attempts indicate a storm (tight loop).

        Prunes the reconnect_times deque to the tracking window first.
        """
        now = time.time()
        cutoff = now - _STORM_WINDOW_SECONDS
        while self._stats.reconnect_times and self._stats.reconnect_times[0] < cutoff:
            self._stats.reconnect_times.popleft()
        return len(self._stats.reconnect_times) >= self._storm_threshold

    async def _reconnect_loop(self) -> None:
        """Attempt reconnection with exponential backoff.

        On success: clears connectivity repair issues, resets failure counter,
        marks entities available (triggers HA state update).
        On failure: classifies error, checks for storm, creates repair if needed.
        Bridge devices use shorter backoff (_BRIDGE_MAX_BACKOFF).
        Respects _max_reconnect_failures limit (0 = unlimited).
        """
        is_bridge = self._is_bridge_device()
        max_backoff = _BRIDGE_MAX_BACKOFF if is_bridge else _MAX_BACKOFF

        while self._running:
            # Check max reconnect failure limit
            if (
                self._max_reconnect_failures > 0
                and self._consecutive_failures >= self._max_reconnect_failures
            ):
                _LOGGER.error(
                    "Max reconnect failures (%d) reached for %s — giving up",
                    self._max_reconnect_failures,
                    self._device.address,
                )
                self._state = replace(self._state, available=False)
                self._dispatch_update()
                return

            _LOGGER.info(
                "Reconnecting to %s in %.0fs (attempt %d%s)",
                self._device.address,
                self._backoff,
                self._consecutive_failures + 1,
                ", bridge" if is_bridge else "",
            )
            await asyncio.sleep(self._backoff)

            if not self._running:
                break

            try:
                start_time = time.monotonic()
                await self._device.connect()
                response_time = time.monotonic() - start_time
                self._stats.response_times.append(response_time)
                self._stats.connect_time = time.time()
                self._stats.total_reconnects += 1
                self._stats.reconnect_times.append(time.time())
                self._state = replace(
                    self._state,
                    available=True,
                    firmware_version=self._device.firmware_version,
                )
                self._backoff = _BRIDGE_INITIAL_BACKOFF if is_bridge else _INITIAL_BACKOFF
                self._consecutive_failures = 0
                self._stats.storm_detected = False
                self._start_rssi_polling()
                _LOGGER.info("Reconnected to %s (%.2fs)", self._device.address, response_time)
                self._log_connect_metrics(response_time)
                # Clear all connectivity repair issues on successful reconnect
                self._clear_repair_issues_on_recovery()
                # Notify listeners — entities will update to available
                self._dispatch_update()
                return
            except Exception as err:
                self._stats.total_errors += 1
                self._stats.connection_errors += 1
                self._stats.last_error = str(err)
                self._stats.last_error_time = time.time()
                self._consecutive_failures += 1
                error_class = self._classify_error(err)
                self._stats.last_error_class = error_class.value
                self._stats.reconnect_times.append(time.time())

                _LOGGER.warning(
                    "Reconnect failed for %s (class=%s, consecutive=%d)",
                    self._device.address,
                    error_class.value,
                    self._consecutive_failures,
                    exc_info=True,
                )

                # Permanent errors should not retry
                # PLAT-402 Task 1.3: Set availability state based on error class
                if error_class == ErrorClass.PERMANENT:
                    _LOGGER.error(
                        "Permanent error for %s — stopping reconnect",
                        self._device.address,
                    )
                    self._state = replace(
                        self._state,
                        available=False,
                        device_availability=DeviceAvailabilityState.REPROVISION_REQUIRED.value,
                        degraded_reason=f"Permanent error: {error_class.value}",
                    )
                    self._dispatch_update()
                    return

                # Create specific repair issue for this error class (once per recovery)
                self._maybe_create_repair_issue(error_class)

                # Detect reconnect storm and create repair issue (once per storm)
                if (
                    self._hass is not None
                    and self._entry_id is not None
                    and self._check_reconnect_storm()
                    and not self._stats.storm_detected
                ):
                    self._stats.storm_detected = True
                    from custom_components.tuya_ble_mesh.repairs import (
                        async_create_issue_reconnect_storm,
                    )

                    storm_task = asyncio.create_task(
                        async_create_issue_reconnect_storm(
                            self._hass,
                            self.entry_name or self._device.address,
                            len(self._stats.reconnect_times),
                            self._entry_id,
                            _STORM_WINDOW_SECONDS // 60,
                        )
                    )
                    storm_task.add_done_callback(self._log_task_exception)

                # Record reconnect event for timeline diagnostics.
                # deque(maxlen=_RECONNECT_TIMELINE_MAX) evicts oldest automatically.
                self._stats.reconnect_timeline.append(
                    ReconnectEvent(
                        timestamp=time.time(),
                        error_class=error_class.value,
                        backoff=self._backoff,
                        attempt=self._consecutive_failures,
                    )
                )

                # PLAT-402 Task 1.3: Set UNREACHABLE state on reconnect failure
                availability_state = DeviceAvailabilityState.UNREACHABLE.value
                if error_class == ErrorClass.MESH_AUTH:
                    availability_state = DeviceAvailabilityState.REPROVISION_REQUIRED.value

                self._state = replace(
                    self._state,
                    available=False,
                    device_availability=availability_state,
                    degraded_reason=f"{error_class.value}: {str(err)[:100]}",
                )
                self._backoff = min(self._backoff * _BACKOFF_MULTIPLIER, max_backoff)
                self._dispatch_update()

    # --- Adaptive polling ---

    def _adjust_polling_interval(self) -> None:
        """Adjust RSSI polling interval based on state change frequency.

        Decreases interval when values change frequently (more responsive),
        increases when stable (lower overhead).
        """
        # More changes = shorter interval (faster polling)
        if self._state_change_counter >= 2:
            # Frequent changes detected
            self._rssi_interval = max(
                _RSSI_MIN_INTERVAL,
                self._rssi_interval * 0.75,  # Decrease by 25%
            )
            _LOGGER.debug(
                "Adaptive polling: frequent changes detected, interval=%.1fs",
                self._rssi_interval,
            )
        elif self._stable_cycles >= _RSSI_STABILITY_THRESHOLD:
            # Stable state - increase interval
            self._rssi_interval = min(
                _RSSI_MAX_INTERVAL,
                self._rssi_interval * 1.5,  # Increase by 50%
            )
            _LOGGER.debug(
                "Adaptive polling: stable state, interval=%.1fs",
                self._rssi_interval,
            )

        # Reset change counter after adjustment
        self._state_change_counter = 0

    # --- RSSI polling ---

    def _is_bridge_device(self) -> bool:
        """Return True if device communicates via HTTP bridge (no local BLE)."""
        type_name = type(self._device).__name__
        return "Bridge" in type_name

    def _start_rssi_polling(self) -> None:
        """Start periodic RSSI refresh via BLE scan."""
        if self._is_bridge_device():
            return  # No local BLE — skip RSSI polling
        self._stop_rssi_polling()
        try:
            self._rssi_task = asyncio.create_task(self._rssi_loop())
            self._rssi_task.add_done_callback(self._log_task_exception)
        except RuntimeError:
            pass  # No event loop in standalone/test context

    def _stop_rssi_polling(self) -> None:
        """Stop RSSI polling."""
        if self._rssi_task is not None:
            self._rssi_task.cancel()
            self._rssi_task = None

    async def _rssi_loop(self) -> None:
        """Periodically update RSSI using HA's bluetooth integration.

        Uses async_ble_device_from_address() when hass is available (preferred,
        reads cached BLE advertisement data without extra scanning). Falls back to
        BleakScanner only when running outside of HA (e.g. tests / standalone).
        """
        try:
            while self._running and self._state.available:
                await asyncio.sleep(self._rssi_interval)
                if not self._running or not self._state.available:
                    break

                try:
                    prev_rssi = self._state.rssi
                    ble_device = None

                    if self._hass is not None:
                        # Preferred path: use HA bluetooth stack (no extra scan)
                        from homeassistant.components.bluetooth import (
                            async_ble_device_from_address,
                        )

                        ble_device = async_ble_device_from_address(
                            self._hass, self._device.address, connectable=False
                        )
                    else:
                        # Standalone / test fallback only
                        from bleak import BleakScanner

                        ble_device = await BleakScanner.find_device_by_address(
                            self._device.address, timeout=10.0
                        )

                    if ble_device is not None and ble_device.rssi is not None:  # type: ignore[attr-defined]
                        self._state = replace(self._state, rssi=ble_device.rssi)  # type: ignore[attr-defined]
                        self._stats.rssi_history.append((time.time(), ble_device.rssi))  # type: ignore[attr-defined]
                        self._dispatch_update()

                        # Track stability: if RSSI similar (±2 dBm) = stable cycle
                        if prev_rssi is not None and abs(ble_device.rssi - prev_rssi) <= 2:  # type: ignore[attr-defined]
                            self._stable_cycles += 1
                            if self._stable_cycles >= _RSSI_STABILITY_THRESHOLD:
                                self._adjust_polling_interval()
                        else:
                            self._stable_cycles = 0

                except Exception:
                    _LOGGER.debug("RSSI update failed (ignored)", exc_info=True)
        except asyncio.CancelledError:
            pass

    # --- BLE command retry ---

    async def send_command_with_retry(
        self,
        coro_func: Callable[[], Any],
        *,
        max_retries: int | None = None,
        base_delay: float | None = None,
        description: str = "command",
    ) -> None:
        """Execute a device command coroutine with exponential-backoff retry.

        Retries on any exception up to ``max_retries`` times. Each retry waits
        ``base_delay * 2^(attempt-1)`` seconds (e.g. 0.5s, 1s, 2s for 3 retries).
        Logs each retry attempt at WARNING level.

        Args:
            coro_func: Callable that returns a coroutine (e.g. ``lambda: device.send_power(True)``).
            max_retries: Override for maximum retry attempts. Defaults to
                ``DEFAULT_MAX_COMMAND_RETRIES`` from const.
            base_delay: Override for base retry delay in seconds. Defaults to
                ``DEFAULT_COMMAND_RETRY_BASE_DELAY`` from const.
            description: Human-readable label for log messages (e.g. "send_power(True)").

        Raises:
            The last exception if all retries are exhausted.
        """
        from custom_components.tuya_ble_mesh.const import (
            DEFAULT_COMMAND_RETRY_BASE_DELAY,
            DEFAULT_MAX_COMMAND_RETRIES,
        )

        _max = max_retries if max_retries is not None else DEFAULT_MAX_COMMAND_RETRIES
        _delay = base_delay if base_delay is not None else DEFAULT_COMMAND_RETRY_BASE_DELAY

        async with self._command_semaphore:
            last_exc: Exception | None = None
            for attempt in range(1, _max + 1):
                try:
                    await coro_func()
                    return  # Success
                except Exception as exc:
                    last_exc = exc
                    self._stats.command_errors += 1
                    self._stats.total_errors += 1
                    if attempt < _max:
                        wait = _delay * (2 ** (attempt - 1))
                        _LOGGER.warning(
                            "BLE command '%s' failed for %s (attempt %d/%d) — "
                            "retrying in %.1fs: %s",
                            description,
                            self._device.address,
                            attempt,
                            _max,
                            wait,
                            exc,
                        )
                        await asyncio.sleep(wait)
                    else:
                        _LOGGER.error(
                            "BLE command '%s' failed for %s after %d attempts: %s",
                            description,
                            self._device.address,
                            _max,
                            exc,
                        )

            if last_exc is not None:
                raise last_exc

    def set_scene_id(self, scene_id: int) -> None:
        """Optimistically update the active scene ID in the coordinator state.

        Called by the light entity immediately after sending send_scene() to the
        device, so the HA UI reflects the new scene without waiting for a
        state-notification callback.

        Args:
            scene_id: Mesh scene slot number (1-based; 0 = no active scene).
        """
        self._state = replace(self._state, scene_id=scene_id)
        self._dispatch_update()

    def assume_state(self, desired: dict[str, Any], sent: dict[str, Any]) -> None:
        """Optimistically update state after sending a command (PLAT-402 Task 1.2).

        Called by entities after sending a command but before device confirmation.
        Sets state_confidence to low (0.3) and marks source as ASSUMED.

        Args:
            desired: The state the user/automation requested.
            sent: The actual state values sent to the device (after clamping/rounding).
        """
        now = time.time()
        # Apply sent state to current fields (optimistic update)
        # PLAT-402 Task 1.3: Mark as ASSUMED_ONLINE
        updates: dict[str, Any] = {
            "desired_state": MappingProxyType(desired),
            "last_sent_state": MappingProxyType(sent),
            "state_confidence": 0.3,  # Low confidence — not yet confirmed
            "last_update_source": StateUpdateSource.ASSUMED.value,
            "last_update_time": now,
            "device_availability": DeviceAvailabilityState.ASSUMED_ONLINE.value,
        }
        # Merge sent state into current state fields
        if "is_on" in sent:
            updates["is_on"] = sent["is_on"]
        if "brightness" in sent:
            updates["brightness"] = sent["brightness"]
        if "color_temp" in sent:
            updates["color_temp"] = sent["color_temp"]
        if "red" in sent:
            updates["red"] = sent["red"]
        if "green" in sent:
            updates["green"] = sent["green"]
        if "blue" in sent:
            updates["blue"] = sent["blue"]
        if "color_brightness" in sent:
            updates["color_brightness"] = sent["color_brightness"]
        if "mode" in sent:
            updates["mode"] = sent["mode"]

        self._state = replace(self._state, **updates)
        _LOGGER.debug(
            "Assumed state: desired=%s sent=%s confidence=%.2f",
            desired,
            sent,
            0.3,
        )
        self._dispatch_update()
