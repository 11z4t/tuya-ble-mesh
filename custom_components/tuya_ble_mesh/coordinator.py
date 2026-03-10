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
import logging
import statistics
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Union

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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

# Structured logging: MeshLogAdapter injects correlation ID + device MAC into records.
# Falls back to plain _LOGGER if lib is not importable (tests without full lib).
try:
    from tuya_ble_mesh.logging_context import (
        MeshLogAdapter,
        mesh_operation,
    )

    _MESH_LOGGER: logging.Logger | MeshLogAdapter = MeshLogAdapter(logging.getLogger(__name__), {})
    _HAS_MESH_LOGGER = True
except ImportError:  # pragma: no cover — lib always present in production
    _HAS_MESH_LOGGER = False

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


class ErrorClass(str, Enum):
    """Classification of connection/protocol errors for repair creation."""

    BRIDGE_DOWN = "bridge_down"
    DEVICE_OFFLINE = "device_offline"
    MESH_AUTH = "mesh_auth"
    PROTOCOL = "protocol"
    PERMANENT = "permanent"
    TRANSIENT = "transient"
    UNKNOWN = "unknown"

# Sequence number persistence
_SEQ_PERSIST_INTERVAL = 10  # Save seq every N commands
_SEQ_SAFETY_MARGIN = 100  # Add margin on restore to avoid replay
_SEQ_STORE_VERSION = 1

# Listener error tolerance: remove broken callbacks after this many consecutive failures
_MAX_CALLBACK_ERRORS = 3


@dataclass(slots=True)
class TuyaBLEMeshDeviceState:
    """Current state of a Tuya BLE Mesh device.

    Mutable for now (many callers update fields directly).
    Target: frozen=True with dataclasses.replace() in v0.25+.
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

    async def _async_update_data(self) -> None:
        """No-op — state updates arrive via BLE notifications."""
        return None

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

    def _dispatch_update(self) -> None:
        """Thread-safe wrapper for async_set_updated_data.

        BLE notification callbacks (Bleak) may fire on a background thread.
        This ensures state updates are dispatched on the event loop thread.
        """
        if self._hass is not None:
            self._hass.loop.call_soon_threadsafe(self.async_set_updated_data, None)
        else:
            # Standalone / test — assume we're on the event loop
            self.async_set_updated_data(None)

    def _on_onoff_update(self, on: bool) -> None:
        """Handle a GenericOnOff Status from a SIG Mesh device.

        Args:
            on: True if device is on, False if off.
        """
        was_available = self._state.available
        changed = self._state.is_on != on
        self._state.is_on = on
        self._state.available = True
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
            self._seq_persist_task = asyncio.create_task(self._save_seq())

        _LOGGER.debug("OnOff update: on=%s (changed=%s)", on, changed)
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

        self._state.mode = status.mode
        self._state.brightness = status.white_brightness
        self._state.color_temp = status.white_temp
        self._state.red = status.red
        self._state.green = status.green
        self._state.blue = status.blue
        self._state.color_brightness = status.color_brightness
        self._state.is_on = status.white_brightness > 0 or status.color_brightness > 0
        self._state.available = True
        self._backoff = _INITIAL_BACKOFF

        # Adaptive polling: track state changes
        if changed:
            self._state_change_counter += 1
            self._stable_cycles = 0
            self._adjust_polling_interval()

        _LOGGER.debug(
            "Status update: on=%s mode=%d bright=%d temp=%d rgb=(%d,%d,%d) cbright=%d (changed=%s)",
            self._state.is_on,
            self._state.mode,
            self._state.brightness,
            self._state.color_temp,
            self._state.red,
            self._state.green,
            self._state.blue,
            self._state.color_brightness,
            changed,
        )

        # Only notify if state changed or device just became available (avoids
        # spurious HA entity writes when repeated identical status messages arrive)
        if changed or not was_available:
            self._dispatch_update()

    def _on_vendor_update(self, opcode: int, params: bytes) -> None:
        """Handle a Tuya vendor message from a SIG Mesh device.

        Parses vendor DPs for power/energy data.

        Args:
            opcode: 3-byte vendor opcode.
            params: Raw vendor message parameters.
        """
        from tuya_ble_mesh.sig_mesh_protocol import (
            DP_ID_ENERGY_KWH,
            DP_ID_POWER_W,
            TUYA_VENDOR_OPCODE,
            parse_tuya_vendor_dps,
        )

        if opcode != TUYA_VENDOR_OPCODE:
            return

        dps = parse_tuya_vendor_dps(params)
        updated = False
        for dp in dps:
            if dp.dp_id == DP_ID_POWER_W and len(dp.value) >= 1:
                raw = int.from_bytes(dp.value, "big")
                self._state.power_w = raw / 10.0
                updated = True
                _LOGGER.debug("Power: %.1f W (raw=%d)", self._state.power_w, raw)
            elif dp.dp_id == DP_ID_ENERGY_KWH and len(dp.value) >= 1:
                raw = int.from_bytes(dp.value, "big")
                self._state.energy_kwh = raw / 100.0
                updated = True
                _LOGGER.debug("Energy: %.2f kWh (raw=%d)", self._state.energy_kwh, raw)

        if updated:
            self._state.available = True
            self._dispatch_update()

    def _on_composition_update(self, comp: CompositionData) -> None:
        """Handle a Composition Data response from a SIG Mesh device.

        Updates firmware_version in state from device's firmware_version property.

        Args:
            comp: Parsed composition data.
        """
        self._state.firmware_version = self._device.firmware_version
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
        self._state.available = False
        self._stop_rssi_polling()

        # Use bridge-specific backoff if this is a bridge device
        if self._is_bridge_device():
            self._backoff = _BRIDGE_INITIAL_BACKOFF

        self._dispatch_update()
        self._schedule_reconnect()

    async def _load_seq(self) -> None:
        """Load persisted sequence number from HA Store.

        Adds a safety margin to avoid sequence number reuse after crash.
        No-op if hass or entry_id is None.
        """
        if self._hass is None or self._entry_id is None:
            return
        if not hasattr(self._device, "set_seq"):
            return

        from homeassistant.helpers.storage import Store

        self._seq_store = Store(
            self._hass,
            _SEQ_STORE_VERSION,
            f"tuya_ble_mesh.seq.{self._entry_id}",
        )

        data = await self._seq_store.async_load()
        if data is not None and "seq" in data:
            restored_seq = data["seq"] + _SEQ_SAFETY_MARGIN
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
        if self._seq_store is None or not hasattr(self._device, "get_seq"):
            return

        seq = self._device.get_seq()
        await self._seq_store.async_save({"seq": seq})
        _LOGGER.debug("Persisted seq=%d", seq)

    async def async_start(self) -> None:
        """Start the coordinator — connect and begin receiving notifications."""
        self._running = True

        # Restore sequence number before connecting
        await self._load_seq()

        # Wire callbacks based on device type (duck-typing)
        if hasattr(self._device, "register_onoff_callback"):
            self._device.register_onoff_callback(self._on_onoff_update)
        if hasattr(self._device, "register_vendor_callback"):
            self._device.register_vendor_callback(self._on_vendor_update)
        if hasattr(self._device, "register_composition_callback"):
            self._device.register_composition_callback(self._on_composition_update)
        if hasattr(self._device, "register_status_callback"):
            self._device.register_status_callback(self._on_status_update)
        self._device.register_disconnect_callback(self._on_disconnect)

        try:
            start_time = time.monotonic()
            await self._device.connect()
            response_time = time.monotonic() - start_time
            self._stats.response_times.append(response_time)
            self._stats.connect_time = time.time()
            self._state.available = True
            self._state.firmware_version = self._device.firmware_version
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
            self._state.available = False
            self._schedule_reconnect()

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
                try:
                    getattr(self._device, attr)(callback)
                except (ValueError, AttributeError):
                    pass  # Already unregistered or device gone
        try:
            self._device.unregister_disconnect_callback(self._on_disconnect)
        except (ValueError, AttributeError):
            pass

        try:
            await self._device.disconnect()
        except Exception:
            _LOGGER.debug("Disconnect error during stop (ignored)", exc_info=True)

        self._state.available = False
        _LOGGER.info("Coordinator stopped for %s", self._device.address)

    def _schedule_reconnect(self) -> None:
        """Schedule a reconnection attempt with exponential backoff."""
        if not self._running:
            return

        if self._reconnect_task is not None:
            self._reconnect_task.cancel()

        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    def _classify_error(self, err: Exception) -> ErrorClass:
        """Classify a connection error into a category for repair creation.

        Uses exception type name and message keywords to determine root cause.
        """
        err_type = type(err).__name__.lower()
        err_msg = str(err).lower()

        if "timeout" in err_type or "timeout" in err_msg:
            return ErrorClass.TRANSIENT
        if "auth" in err_type or "auth" in err_msg or "password" in err_msg or "credential" in err_msg:
            return ErrorClass.MESH_AUTH
        if "protocol" in err_type or "protocol" in err_msg or "version" in err_msg:
            return ErrorClass.PROTOCOL
        if "connection refused" in err_msg or "unreachable" in err_msg or "no route" in err_msg:
            return ErrorClass.BRIDGE_DOWN
        if "not found" in err_msg or "device not found" in err_msg:
            return ErrorClass.DEVICE_OFFLINE
        if "vendor" in err_msg or "unsupported" in err_msg:
            return ErrorClass.PERMANENT
        return ErrorClass.UNKNOWN

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
                self._state.available = False
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
                self._state.available = True
                self._state.firmware_version = self._device.firmware_version
                self._backoff = _BRIDGE_INITIAL_BACKOFF if is_bridge else _INITIAL_BACKOFF
                self._consecutive_failures = 0
                self._stats.storm_detected = False
                self._start_rssi_polling()
                _LOGGER.info("Reconnected to %s (%.2fs)", self._device.address, response_time)
                self._log_connect_metrics(response_time)
                # Clear connectivity repair issues on successful reconnect
                if self._hass is not None:
                    from custom_components.tuya_ble_mesh.repairs import (
                        ISSUE_BRIDGE_UNREACHABLE,
                        ISSUE_DEVICE_NOT_FOUND,
                        ISSUE_RECONNECT_STORM,
                        ISSUE_TIMEOUT,
                        async_delete_issue,
                    )
                    for issue_id in (
                        ISSUE_BRIDGE_UNREACHABLE,
                        ISSUE_DEVICE_NOT_FOUND,
                        ISSUE_TIMEOUT,
                        ISSUE_RECONNECT_STORM,
                    ):
                        async_delete_issue(self._hass, issue_id)
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
                if error_class == ErrorClass.PERMANENT:
                    _LOGGER.error(
                        "Permanent error for %s — stopping reconnect",
                        self._device.address,
                    )
                    self._state.available = False
                    self._dispatch_update()
                    return

                # Detect reconnect storm and create repair issue (once per storm)
                if self._hass is not None and self._check_reconnect_storm() and not self._stats.storm_detected:
                    self._stats.storm_detected = True
                    from custom_components.tuya_ble_mesh.repairs import (
                        async_create_issue_reconnect_storm,
                    )
                    asyncio.create_task(
                        async_create_issue_reconnect_storm(
                            self._hass,
                            self.entry_name or self._device.address,
                            len(self._stats.reconnect_times),
                            _STORM_WINDOW_SECONDS // 60,
                        )
                    )

                self._state.available = False
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
        self._rssi_task = asyncio.create_task(self._rssi_loop())

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
                        self._state.rssi = ble_device.rssi  # type: ignore[attr-defined]
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
