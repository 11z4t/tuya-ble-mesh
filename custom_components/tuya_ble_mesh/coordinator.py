"""Push-based coordinator for Tuya BLE Mesh devices.

Subclasses DataUpdateCoordinator with update_interval=None (push-based).
BLE notifications drive state updates via async_set_updated_data().
Connection lifecycle delegated to ConnectionManager (PLAT-667).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Union

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from custom_components.tuya_ble_mesh.connection_manager import (
    ConnectionManager,
    ConnectionStatistics,
)
from custom_components.tuya_ble_mesh.device_capabilities import DeviceCapabilities
from custom_components.tuya_ble_mesh.error_classifier import ErrorClass

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store
    from tuya_ble_mesh.device import MeshDevice
    from tuya_ble_mesh.protocol import StatusResponse
    from tuya_ble_mesh.sig_mesh_bridge import SIGMeshBridgeDevice, TelinkBridgeDevice
    from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice
    from tuya_ble_mesh.sig_mesh_protocol import CompositionData

AnyMeshDevice = Union["MeshDevice", "SIGMeshDevice", "TelinkBridgeDevice", "SIGMeshBridgeDevice"]

_LOGGER = logging.getLogger(__name__)
_MAX_CALLBACK_ERRORS = 3
_SEQ_PERSIST_INTERVAL = 10
_SEQ_SAFETY_MARGIN = 100
_SEQ_STORE_VERSION = 1
_INITIAL_BACKOFF = 5.0  # backward-compat alias
_DEBOUNCE_DELAY = 1.5  # PLAT-754: backward-compat alias for connection_manager.DEBOUNCE_DELAY
_STALENESS_THRESHOLD_SECONDS = 300  # 5 minutes
_STALENESS_CHECK_INTERVAL = 60  # Check every minute
# Backward-compat aliases — sourced from connection_manager, re-exported for tests
_BACKOFF_MULTIPLIER: float = 2.0
_BRIDGE_INITIAL_BACKOFF: float = 3.0
_BRIDGE_MAX_BACKOFF: float = 120.0
_MAX_BACKOFF: float = 300.0
_STORM_WINDOW_SECONDS: int = 300
_RSSI_DEFAULT_INTERVAL: float = 60.0
_RSSI_MAX_INTERVAL: float = 300.0
_RSSI_MIN_INTERVAL: float = 30.0
_RSSI_STABILITY_THRESHOLD: int = 3
_COMMAND_CONCURRENCY_LIMIT: int = 5


class StateUpdateSource(StrEnum):
    """Source of a device state update for confidence tracking."""

    NOTIFY = "notify"
    POLL = "poll"
    COMMAND_ECHO = "command_echo"
    ASSUMED = "assumed"


class DeviceAvailabilityState(StrEnum):
    """Per-device availability state (Phase 1 Task 1.3)."""

    UNKNOWN = "unknown"
    AVAILABLE = "available"
    STALE = "stale"
    ASSUMED_ONLINE = "assumed_online"
    UNREACHABLE = "unreachable"
    REPROVISION_REQUIRED = "reprovision_required"


@dataclass(frozen=True, slots=True)
class TuyaBLEMeshDeviceState:
    """Immutable snapshot of a Tuya BLE Mesh device state."""

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
    scene_id: int = 0
    last_seen: float | None = None
    desired_state: MappingProxyType[str, Any] = field(default_factory=lambda: MappingProxyType({}))
    last_sent_state: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    last_confirmed_state: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    state_confidence: float = 0.0
    last_update_source: str = StateUpdateSource.ASSUMED.value
    last_update_time: float | None = None
    device_availability: str = DeviceAvailabilityState.UNKNOWN.value
    consecutive_write_failures: int = 0
    degraded_reason: str | None = None


class TuyaBLEMeshCoordinator(DataUpdateCoordinator[None]):  # type: ignore[misc]
    """Push-based coordinator for a single BLE mesh device."""

    def __init__(
        self,
        device: AnyMeshDevice,
        *,
        hass: HomeAssistant | None = None,
        entry_id: str | None = None,
        entry: ConfigEntry | None = None,
    ) -> None:
        if hass is not None:
            super().__init__(
                hass, _LOGGER, name=f"tuya_ble_mesh_{device.address}", update_interval=None
            )
        self._device: AnyMeshDevice = device
        self.capabilities = DeviceCapabilities.from_device(device)
        self._state = TuyaBLEMeshDeviceState()
        self._hass = hass
        self._entry_id = entry_id
        self._entry = entry
        self._seq_store: Store[dict[str, int]] | None = None
        self._seq_command_count = 0
        self._seq_persist_task: asyncio.Task[None] | None = None
        self._standalone_listeners: list[Callable[[], None]] = []
        self._listener_error_counts: dict[int, int] = {}
        self._conn_mgr = ConnectionManager(
            device,
            hass=hass,
            entry_id=entry_id,
            on_connected=self._handle_reconnected,
            on_state_update=self._handle_conn_state_update,
        )
        self._staleness_task: asyncio.Task[None] | None = None

    # --- Explicit delegation to ConnectionManager (no magic methods) ---

    @property
    def backoff(self) -> float:
        """Current reconnect backoff delay in seconds.

        Returns:
            Backoff delay in seconds before next reconnect attempt.
        """
        return self._conn_mgr.backoff

    @backoff.setter
    def backoff(self, value: float) -> None:
        """Set reconnect backoff delay.

        Args:
            value: New backoff delay in seconds.
        """
        self._conn_mgr.backoff = value

    @property
    def _backoff(self) -> float:
        """Current reconnect backoff delay (private accessor).

        Returns:
            Backoff delay in seconds.
        """
        return self._conn_mgr._backoff

    @_backoff.setter
    def _backoff(self, value: float) -> None:
        """Set reconnect backoff delay (private accessor).

        Args:
            value: New backoff delay in seconds.
        """
        self._conn_mgr._backoff = value

    @property
    def running(self) -> bool:
        """Whether the connection manager is actively running.

        Returns:
            True if connection manager is running, False otherwise.
        """
        return self._conn_mgr.running

    @running.setter
    def running(self, value: bool) -> None:
        """Set running state.

        Args:
            value: New running state.
        """
        self._conn_mgr.running = value

    @property
    def _running(self) -> bool:
        """Whether the connection manager is actively running (private accessor).

        Returns:
            Running state.
        """
        return self._conn_mgr._running

    @_running.setter
    def _running(self, value: bool) -> None:
        """Set running state (private accessor).

        Args:
            value: New running state.
        """
        self._conn_mgr._running = value

    @property
    def latest_rssi(self) -> int | None:
        """Most recent RSSI value from device.

        Returns:
            RSSI in dBm, or None if not yet measured.
        """
        return self._conn_mgr.latest_rssi

    @property
    def _rssi_interval(self) -> float:
        """Current RSSI polling interval in seconds.

        Returns:
            RSSI polling interval in seconds.
        """
        return self._conn_mgr._rssi_interval

    @_rssi_interval.setter
    def _rssi_interval(self, value: float) -> None:
        """Set RSSI polling interval.

        Args:
            value: New interval in seconds.
        """
        self._conn_mgr._rssi_interval = value

    @property
    def _stable_cycles(self) -> int:
        """Number of consecutive stable polling cycles.

        Returns:
            Count of stable cycles without state changes.
        """
        return self._conn_mgr._stable_cycles

    @_stable_cycles.setter
    def _stable_cycles(self, value: int) -> None:
        """Set stable cycles count.

        Args:
            value: New count.
        """
        self._conn_mgr._stable_cycles = value

    @property
    def _state_change_counter(self) -> int:
        """Number of state changes in current polling window.

        Returns:
            State change count.
        """
        return self._conn_mgr._state_change_counter

    @_state_change_counter.setter
    def _state_change_counter(self, value: int) -> None:
        """Set state change counter.

        Args:
            value: New count.
        """
        self._conn_mgr._state_change_counter = value

    @property
    def _reconnect_task(self) -> asyncio.Task[None] | None:
        """Current reconnect task, if any.

        Returns:
            Reconnect task or None.
        """
        return self._conn_mgr._reconnect_task

    @_reconnect_task.setter
    def _reconnect_task(self, value: asyncio.Task[None] | None) -> None:
        """Set reconnect task.

        Args:
            value: New task or None.
        """
        self._conn_mgr._reconnect_task = value

    @property
    def _rssi_task(self) -> asyncio.Task[None] | None:
        """Current RSSI polling task, if any.

        Returns:
            RSSI task or None.
        """
        return self._conn_mgr._rssi_task

    @_rssi_task.setter
    def _rssi_task(self, value: asyncio.Task[None] | None) -> None:
        """Set RSSI task.

        Args:
            value: New task or None.
        """
        self._conn_mgr._rssi_task = value

    @property
    def _consecutive_failures(self) -> int:
        """Number of consecutive connection failures.

        Returns:
            Consecutive failure count.
        """
        return self._conn_mgr._consecutive_failures

    @_consecutive_failures.setter
    def _consecutive_failures(self, value: int) -> None:
        """Set consecutive failures count.

        Args:
            value: New count.
        """
        self._conn_mgr._consecutive_failures = value

    @property
    def _storm_threshold(self) -> int:
        """Reconnect storm detection threshold.

        Returns:
            Storm threshold count.
        """
        return self._conn_mgr._storm_threshold

    @_storm_threshold.setter
    def _storm_threshold(self, value: int) -> None:
        """Set storm threshold.

        Args:
            value: New threshold.
        """
        self._conn_mgr._storm_threshold = value

    @property
    def _max_reconnect_failures(self) -> int:
        """Maximum reconnect failures before giving up.

        Returns:
            Max failures (0 = unlimited).
        """
        return self._conn_mgr._max_reconnect_failures

    @_max_reconnect_failures.setter
    def _max_reconnect_failures(self, value: int) -> None:
        """Set max reconnect failures.

        Args:
            value: New max (0 = unlimited).
        """
        self._conn_mgr._max_reconnect_failures = value

    @property
    def _raised_repair_issues(self) -> set[str]:
        """Set of repair issue IDs that have been raised.

        Returns:
            Set of raised issue IDs.
        """
        return self._conn_mgr._raised_repair_issues

    def _clear_repair_issues_on_recovery(self) -> None:
        """Clear all raised repair issues on recovery (delegates to ConnectionManager)."""
        self._conn_mgr._clear_repair_issues_on_recovery()

    @property
    def _stats(self) -> ConnectionStatistics:
        """Connection statistics (direct access).

        Returns:
            ConnectionStatistics instance.
        """
        return self._conn_mgr._stats

    def classify_error(self, err: Exception) -> ErrorClass:
        """Classify an exception into an ErrorClass category.

        Args:
            err: Exception to classify.

        Returns:
            ErrorClass category for the exception.
        """
        return self._conn_mgr.classify_error(err)

    def _classify_error(self, err: Exception) -> ErrorClass:
        """Classify an exception (private accessor).

        Args:
            err: Exception to classify.

        Returns:
            ErrorClass category for the exception.
        """
        return self._conn_mgr.classify_error(err)

    def is_bridge_device(self) -> bool:
        """Check if the device is a bridge (HTTP-based).

        Returns:
            True if device is a bridge, False otherwise.
        """
        return self._conn_mgr.is_bridge_device()

    def adjust_polling_interval(self) -> None:
        """Adjust RSSI polling interval based on device stability."""
        self._conn_mgr.adjust_polling_interval()

    def start_rssi_polling(self) -> None:
        """Start adaptive RSSI polling task."""
        self._conn_mgr.start_rssi_polling()

    def _start_rssi_polling(self) -> None:
        """Start adaptive RSSI polling task (private alias)."""
        self._conn_mgr.start_rssi_polling()

    def stop_rssi_polling(self) -> None:
        """Stop RSSI polling task."""
        self._conn_mgr.stop_rssi_polling()

    def _stop_rssi_polling(self) -> None:
        """Stop RSSI polling task (private alias)."""
        self._conn_mgr.stop_rssi_polling()

    def _check_reconnect_storm(self) -> bool:
        """Check if reconnect storm is detected.

        Returns:
            True if storm detected, False otherwise.
        """
        return self._conn_mgr._check_reconnect_storm()

    def record_connection_error(self, err: Exception) -> None:
        """Record a connection error in statistics.

        Args:
            err: The exception that occurred.
        """
        self._conn_mgr.record_connection_error(err)

    def _log_connect_metrics(self, response_time: float) -> None:
        """Log connection metrics.

        Args:
            response_time: Connection response time in seconds.
        """
        self._conn_mgr._log_connect_metrics(response_time)

    async def _rssi_loop(self) -> None:
        """RSSI polling loop (delegated to ConnectionManager)."""
        await self._conn_mgr._rssi_loop()

    async def _reconnect_loop(self) -> None:
        """Reconnect loop (delegated to ConnectionManager)."""
        await self._conn_mgr._reconnect_loop()

    async def _staleness_watchdog_loop(self) -> None:
        """Watchdog loop to detect stale devices (PLAT-746).

        Periodically checks if device has stopped sending notifications.
        If no update for _STALENESS_THRESHOLD_SECONDS, marks device unavailable.
        """
        _LOGGER.debug("Starting staleness watchdog for %s", self._device.address)
        while self._conn_mgr.running:
            try:
                await asyncio.sleep(_STALENESS_CHECK_INTERVAL)

                if not self._state.available:
                    # Already marked unavailable, skip check
                    continue

                last_update = self._state.last_seen
                if last_update is None:
                    # No updates yet, wait for first notification
                    continue

                now = time.time()
                time_since_update = now - last_update

                if time_since_update > _STALENESS_THRESHOLD_SECONDS:
                    _LOGGER.warning(
                        "Device %s is stale (%.1fs since last update, threshold: %.1fs)",
                        self._device.address,
                        time_since_update,
                        _STALENESS_THRESHOLD_SECONDS,
                    )

                    # Try to probe device before marking unavailable
                    probe_success = await self._probe_device()

                    if not probe_success:
                        _LOGGER.warning(
                            "Device %s probe failed, marking unavailable",
                            self._device.address,
                        )
                        self._state = replace(
                            self._state,
                            available=False,
                            device_availability=DeviceAvailabilityState.STALE.value,
                            degraded_reason="No updates received in 5 minutes",
                        )
                        self._dispatch_update()
                    else:
                        _LOGGER.info("Device %s probe succeeded, still alive", self._device.address)

            except asyncio.CancelledError:
                _LOGGER.debug("Staleness watchdog cancelled for %s", self._device.address)
                raise
            except Exception:
                _LOGGER.exception("Error in staleness watchdog for %s", self._device.address)

    async def _probe_device(self) -> bool:
        """Probe device to check if it's still responsive.

        Returns:
            True if device responded, False otherwise.
        """
        try:
            # For BLE devices, attempt to read RSSI
            if hasattr(self._device, "get_rssi"):
                rssi = await asyncio.wait_for(self._device.get_rssi(), timeout=5.0)
                if rssi is not None:
                    _LOGGER.debug("Device %s probe: RSSI=%d", self._device.address, rssi)
                    # Update last_seen since device responded
                    now = time.time()
                    self._state = replace(self._state, last_seen=now, rssi=rssi)
                    return True

            # For devices without RSSI, try connection check
            if hasattr(self._device, "is_connected") and self._device.is_connected:
                _LOGGER.debug("Device %s probe: connection active", self._device.address)
                now = time.time()
                self._state = replace(self._state, last_seen=now)
                return True

            return False

        except TimeoutError:
            _LOGGER.debug("Device %s probe timeout", self._device.address)
            return False
        except Exception:
            _LOGGER.debug("Device %s probe exception", self._device.address, exc_info=True)
            return False

    async def _async_update_data(self) -> None:
        return None

    # --- Properties (forwarded from ConnectionManager) ---

    @property
    def consecutive_failures(self) -> int:
        return self._conn_mgr.consecutive_failures

    @property
    def storm_threshold(self) -> int:
        return self._conn_mgr.storm_threshold

    @property
    def is_connected(self) -> bool:
        return self._state.available

    @property
    def device(self) -> AnyMeshDevice:
        return self._device

    @property
    def state(self) -> TuyaBLEMeshDeviceState:
        return self._state

    @property
    def statistics(self) -> ConnectionStatistics:
        return self._conn_mgr.statistics

    @property
    def avg_response_time_ms(self) -> float | None:
        return self._conn_mgr.avg_response_time_ms()

    @property
    def entry_name(self) -> str:
        return self._conn_mgr.entry_name

    @entry_name.setter
    def entry_name(self, value: str) -> None:
        self._conn_mgr.entry_name = value

    def schedule_reconnect(self) -> None:
        self._conn_mgr.schedule_reconnect()

    async def send_command_with_retry(
        self,
        coro_func: Callable[[], Any],
        *,
        max_retries: int | None = None,
        base_delay: float | None = None,
        description: str = "command",
    ) -> None:
        await self._conn_mgr.send_command_with_retry(
            coro_func, max_retries=max_retries, base_delay=base_delay, description=description
        )

    # --- Listeners ---

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._standalone_listeners.append(listener)

        def _remove() -> None:
            with contextlib.suppress(ValueError):
                self._standalone_listeners.remove(listener)
            self._listener_error_counts.pop(id(listener), None)

        return _remove

    def _notify_listeners(self) -> None:
        for listener in list(self._standalone_listeners):
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
        if self._hass is not None:
            # PLAT-747: Use entry.async_create_background_task for tracked task lifecycle
            if self._entry is not None:
                self._hass.loop.call_soon_threadsafe(
                    lambda: self._entry.async_create_background_task(
                        self._hass,
                        self.async_set_updated_data(None),
                        "dispatch_update",
                        eager_start=True,
                    )
                )
            else:
                # Fallback for standalone mode (no entry)
                self._hass.loop.call_soon_threadsafe(
                    lambda: self._hass.async_create_task(self.async_set_updated_data(None))
                )
        else:
            self._notify_listeners()

    # --- ConnectionManager callbacks ---

    def _handle_reconnected(self, response_time: float) -> None:
        self._state = replace(
            self._state, available=True, firmware_version=self._device.firmware_version
        )
        self.start_rssi_polling()
        self._dispatch_update()

    def _handle_conn_state_update(self) -> None:
        ec = self._conn_mgr.statistics.last_error_class
        if ec == ErrorClass.PERMANENT.value:
            self._state = replace(
                self._state,
                available=False,
                device_availability=DeviceAvailabilityState.REPROVISION_REQUIRED.value,
                degraded_reason=f"Permanent error: {ec}",
            )
        elif not self._state.available and self._conn_mgr.consecutive_failures > 0:
            avail = DeviceAvailabilityState.UNREACHABLE.value
            if ec == ErrorClass.MESH_AUTH.value:
                avail = DeviceAvailabilityState.REPROVISION_REQUIRED.value
            self._state = replace(
                self._state,
                available=False,
                device_availability=avail,
                degraded_reason=f"{ec}: {(self._conn_mgr.statistics.last_error or '')[:100]}",
            )
        rssi = self._conn_mgr.latest_rssi
        if rssi is not None and rssi != self._state.rssi:
            self._state = replace(self._state, rssi=rssi)
        self._dispatch_update()

    # --- BLE notification callbacks ---

    def _make_notify_state(self, now: float, **fields: Any) -> TuyaBLEMeshDeviceState:
        """Build state update with standard notify fields."""
        return replace(
            self._state,
            available=True,
            last_seen=now,
            state_confidence=1.0,
            last_update_source=StateUpdateSource.NOTIFY.value,
            last_update_time=now,
            device_availability=DeviceAvailabilityState.AVAILABLE.value,
            consecutive_write_failures=0,
            degraded_reason=None,
            **fields,
        )

    def _on_onoff_update(self, on: bool) -> None:
        was_available = self._state.available
        changed = self._state.is_on != on
        now = time.time()
        self._state = self._make_notify_state(
            now, is_on=on, last_confirmed_state=MappingProxyType({"is_on": on})
        )
        self._conn_mgr.backoff = _INITIAL_BACKOFF
        if changed:
            self._conn_mgr.record_state_change()
        self._maybe_persist_seq()
        if changed or not was_available:
            self._dispatch_update()

    def _on_status_update(self, status: StatusResponse) -> None:
        was_available = self._state.available
        now = time.time()
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
        confirmed = MappingProxyType(
            {
                "is_on": is_on,
                "mode": status.mode,
                "brightness": status.white_brightness,
                "color_temp": status.white_temp,
                "red": status.red,
                "green": status.green,
                "blue": status.blue,
                "color_brightness": status.color_brightness,
            }
        )
        self._state = self._make_notify_state(
            now,
            mode=status.mode,
            brightness=status.white_brightness,
            color_temp=status.white_temp,
            red=status.red,
            green=status.green,
            blue=status.blue,
            color_brightness=status.color_brightness,
            is_on=is_on,
            last_confirmed_state=confirmed,
        )
        self._conn_mgr.backoff = _INITIAL_BACKOFF
        if changed:
            self._conn_mgr.record_state_change()
        if changed or not was_available:
            self._dispatch_update()

    def _on_vendor_update(self, opcode: int, params: bytes) -> None:
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
        if frame.command == TUYA_CMD_TIMESTAMP_SYNC:
            _LOGGER.info("Device requested timestamp sync — sending response")
            self.hass.async_create_task(self._send_timestamp_response())
            return
        power_w, energy_kwh, updated = self._state.power_w, self._state.energy_kwh, False
        for dp in frame.dps:
            if dp.dp_id == DP_ID_POWER_W and len(dp.value) >= 1:
                power_w = int.from_bytes(dp.value, "big") / 10.0
                updated = True
            elif dp.dp_id == DP_ID_ENERGY_KWH and len(dp.value) >= 1:
                energy_kwh = int.from_bytes(dp.value, "big") / 100.0
                updated = True
        if updated:
            now = time.time()
            cd = dict(self._state.last_confirmed_state)
            if power_w is not None:
                cd["power_w"] = power_w
            if energy_kwh is not None:
                cd["energy_kwh"] = energy_kwh
            self._state = replace(
                self._state,
                power_w=power_w,
                energy_kwh=energy_kwh,
                available=True,
                last_confirmed_state=MappingProxyType(cd),
                state_confidence=1.0,
                last_update_source=StateUpdateSource.NOTIFY.value,
                last_update_time=now,
            )
            self._dispatch_update()

    async def _send_timestamp_response(self) -> None:
        from tuya_ble_mesh.sig_mesh_protocol import tuya_vendor_timestamp_response

        try:
            await self._device.send_vendor_command(tuya_vendor_timestamp_response())
        except Exception:
            _LOGGER.warning("Failed to send timestamp sync response", exc_info=True)

    def _on_composition_update(self, comp: CompositionData) -> None:
        self._state = replace(self._state, firmware_version=self._device.firmware_version)
        self._dispatch_update()

    def _on_disconnect(self) -> None:
        """Handle disconnect event.

        PLAT-754: Disconnect handling includes debounce delay in reconnect logic
        to avoid immediate reconnect loops during transient disconnects.
        """
        self._state = replace(self._state, available=False)
        self._conn_mgr.handle_disconnect()
        self._dispatch_update()

    # --- Sequence persistence ---

    def _maybe_persist_seq(self) -> None:
        self._seq_command_count += 1
        if self._seq_command_count >= _SEQ_PERSIST_INTERVAL:
            self._seq_command_count = 0
            try:
                asyncio.get_running_loop()
                # PLAT-747: Use entry.async_create_background_task for tracked task lifecycle
                if self._entry is not None and self._hass is not None:
                    self._seq_persist_task = self._entry.async_create_background_task(
                        self._hass,
                        self._save_seq(),
                        "persist_seq",
                        eager_start=True,
                    )
                else:
                    # Fallback for standalone mode
                    self._seq_persist_task = asyncio.create_task(self._save_seq())
            except RuntimeError:
                pass

    async def _load_seq(self) -> None:
        if self._hass is None or self._entry_id is None:
            return
        if not self.capabilities.has_sig_sequence:
            return
        from homeassistant.helpers.storage import Store

        self._seq_store = Store(
            self._hass, _SEQ_STORE_VERSION, f"tuya_ble_mesh.seq.{self._entry_id}"
        )
        data = await self._seq_store.async_load()
        if data is not None and "seq" in data:
            restored = data["seq"] + _SEQ_SAFETY_MARGIN
            self._device.set_seq(restored)
            _LOGGER.info(
                "Restored seq=%d (stored=%d + margin=%d)", restored, data["seq"], _SEQ_SAFETY_MARGIN
            )

    async def _save_seq(self) -> None:
        if self._seq_store is None or not self.capabilities.has_sig_sequence:
            return
        seq = self._device.get_seq()
        await self._seq_store.async_save({"seq": seq})

    # --- Lifecycle ---

    async def async_initial_connect(self) -> None:
        """Perform initial connection during config entry setup.

        PLAT-743: Called synchronously from async_setup_entry.
        If connection fails, raises exception to propagate to HA Core,
        which will:
        - Show "Retrying setup" in UI instead of "Loaded"
        - Handle retry scheduling with exponential backoff
        - Give users visibility into integration health

        Raises:
            Exception: Any connection or authentication error from device.connect().
        """
        self._conn_mgr.running = True
        await self._load_seq()
        if self.capabilities.has_onoff_callback:
            self._device.register_onoff_callback(self._on_onoff_update)
        if self.capabilities.has_vendor_callback:
            self._device.register_vendor_callback(self._on_vendor_update)
        if self.capabilities.has_composition_callback:
            self._device.register_composition_callback(self._on_composition_update)
        if self.capabilities.has_status_callback:
            self._device.register_status_callback(self._on_status_update)
        self._device.register_disconnect_callback(self._on_disconnect)

        # Connect and let exceptions propagate to async_setup_entry
        response_time = await self._conn_mgr.async_connect()
        self._state = replace(
            self._state,
            available=True,
            firmware_version=self._device.firmware_version,
            last_seen=time.time(),
        )
        _LOGGER.info(
            "Initial connection succeeded for %s (%.2fs)",
            self._device.address,
            response_time,
        )

        # Start staleness watchdog (PLAT-746, PLAT-747)
        if self._staleness_task is None or self._staleness_task.done():
            if self._entry is not None and self._hass is not None:
                self._staleness_task = self._entry.async_create_background_task(
                    self._hass,
                    self._staleness_watchdog_loop(),
                    "staleness_watchdog",
                    eager_start=True,
                )
            else:
                # Fallback for standalone mode
                self._staleness_task = asyncio.create_task(self._staleness_watchdog_loop())

        self._dispatch_update()

    async def async_start(self) -> None:
        """Start coordinator (legacy method for backward compatibility).

        DEPRECATED: Use async_initial_connect() for initial setup.
        This method swallows exceptions, preventing HA Core from
        seeing connection failures.
        """
        self._conn_mgr.running = True
        await self._load_seq()
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
            response_time = await self._conn_mgr.async_connect()
            self._state = replace(
                self._state,
                available=True,
                firmware_version=self._device.firmware_version,
                last_seen=time.time(),
            )
            _LOGGER.info("Coordinator started for %s (%.2fs)", self._device.address, response_time)

            # Start staleness watchdog (PLAT-746, PLAT-747)
            if self._staleness_task is None or self._staleness_task.done():
                if self._entry is not None and self._hass is not None:
                    self._staleness_task = self._entry.async_create_background_task(
                        self._hass,
                        self._staleness_watchdog_loop(),
                        "staleness_watchdog",
                        eager_start=True,
                    )
                else:
                    # Fallback for standalone mode
                    self._staleness_task = asyncio.create_task(self._staleness_watchdog_loop())

        except Exception as err:
            self._conn_mgr.record_connection_error(err)
            _LOGGER.warning(
                "Initial connection failed for %s, scheduling reconnect",
                self._device.address,
                exc_info=True,
            )
            self._state = replace(self._state, available=False)
            self._conn_mgr.schedule_reconnect()
        self._dispatch_update()

    async def async_stop(self) -> None:
        self._conn_mgr.running = False
        await self._save_seq()

        # Stop staleness watchdog (PLAT-746)
        if self._staleness_task is not None and not self._staleness_task.done():
            self._staleness_task.cancel()
            await asyncio.gather(self._staleness_task, return_exceptions=True)
            self._staleness_task = None

        if self._seq_persist_task is not None:
            self._seq_persist_task.cancel()
            await asyncio.gather(self._seq_persist_task, return_exceptions=True)
            self._seq_persist_task = None
        await self._conn_mgr.async_cancel_tasks()
        for attr, cb in (
            ("unregister_onoff_callback", self._on_onoff_update),
            ("unregister_vendor_callback", self._on_vendor_update),
            ("unregister_composition_callback", self._on_composition_update),
            ("unregister_status_callback", self._on_status_update),
        ):
            if hasattr(self._device, attr):
                with contextlib.suppress(ValueError, AttributeError):
                    getattr(self._device, attr)(cb)
        with contextlib.suppress(ValueError, AttributeError):
            self._device.unregister_disconnect_callback(self._on_disconnect)
        await self._conn_mgr.async_disconnect()
        self._state = replace(self._state, available=False)
        _LOGGER.info("Coordinator stopped for %s", self._device.address)

    # --- State management ---

    def set_scene_id(self, scene_id: int) -> None:
        self._state = replace(self._state, scene_id=scene_id)
        self._dispatch_update()

    def assume_state(self, desired: dict[str, Any], sent: dict[str, Any]) -> None:
        now = time.time()
        updates: dict[str, Any] = {
            "desired_state": MappingProxyType(desired),
            "last_sent_state": MappingProxyType(sent),
            "state_confidence": 0.3,
            "last_update_source": StateUpdateSource.ASSUMED.value,
            "last_update_time": now,
            "device_availability": DeviceAvailabilityState.ASSUMED_ONLINE.value,
        }
        for key in (
            "is_on",
            "brightness",
            "color_temp",
            "red",
            "green",
            "blue",
            "color_brightness",
            "mode",
        ):
            if key in sent:
                updates[key] = sent[key]
        self._state = replace(self._state, **updates)
        self._dispatch_update()
