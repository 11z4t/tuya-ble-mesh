"""Push-based coordinator for Tuya BLE Mesh devices.

Subclasses DataUpdateCoordinator with update_interval=None (push-based).
BLE notifications drive state updates via async_set_updated_data().
Connection lifecycle delegated to ConnectionManager (PLAT-667).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure lib/ is importable (HACS installs only custom_components/)
_lib_path = str(Path(__file__).parent / "lib")
if _lib_path not in sys.path:
    sys.path.insert(0, _lib_path)

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
    BACKOFF_MULTIPLIER as _BACKOFF_MULTIPLIER,
    BRIDGE_INITIAL_BACKOFF as _BRIDGE_INITIAL_BACKOFF,
    BRIDGE_MAX_BACKOFF as _BRIDGE_MAX_BACKOFF,
    COMMAND_CONCURRENCY_LIMIT as _COMMAND_CONCURRENCY_LIMIT,
    ConnectionManager,
    ConnectionStatistics,
    MAX_BACKOFF as _MAX_BACKOFF,
    RSSI_DEFAULT_INTERVAL as _RSSI_DEFAULT_INTERVAL,
    RSSI_MAX_INTERVAL as _RSSI_MAX_INTERVAL,
    RSSI_MIN_INTERVAL as _RSSI_MIN_INTERVAL,
    RSSI_STABILITY_THRESHOLD as _RSSI_STABILITY_THRESHOLD,
    STORM_DEFAULT_THRESHOLD as _STORM_DEFAULT_THRESHOLD,
    STORM_WINDOW_SECONDS as _STORM_WINDOW_SECONDS,
)
from custom_components.tuya_ble_mesh.error_classifier import ErrorClass
from custom_components.tuya_ble_mesh.device_capabilities import DeviceCapabilities

if TYPE_CHECKING:
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
        default_factory=lambda: MappingProxyType({}))
    last_confirmed_state: MappingProxyType[str, Any] = field(
        default_factory=lambda: MappingProxyType({}))
    state_confidence: float = 0.0
    last_update_source: str = StateUpdateSource.ASSUMED.value
    last_update_time: float | None = None
    device_availability: str = DeviceAvailabilityState.UNKNOWN.value
    consecutive_write_failures: int = 0
    degraded_reason: str | None = None


class TuyaBLEMeshCoordinator(DataUpdateCoordinator[None]):
    """Push-based coordinator for a single BLE mesh device."""

    def __init__(
        self, device: AnyMeshDevice, *,
        hass: HomeAssistant | None = None, entry_id: str | None = None,
    ) -> None:
        if hass is not None:
            super().__init__(hass, _LOGGER,
                             name=f"tuya_ble_mesh_{device.address}", update_interval=None)
        self._device: AnyMeshDevice = device
        self.capabilities = DeviceCapabilities.from_device(device)
        self._state = TuyaBLEMeshDeviceState()
        self._hass = hass
        self._entry_id = entry_id
        self._seq_store: Store[dict[str, int]] | None = None
        self._seq_command_count = 0
        self._seq_persist_task: asyncio.Task[None] | None = None
        self._standalone_listeners: list[Callable[[], None]] = []
        self._listener_error_counts: dict[int, int] = {}
        self._conn_mgr = ConnectionManager(
            device, hass=hass, entry_id=entry_id,
            on_connected=self._handle_reconnected,
            on_state_update=self._handle_conn_state_update,
        )

    # --- Backward-compatible proxies for private attrs moved to ConnectionManager ---
    _CONN_ATTRS = frozenset({
        '_backoff', '_stats', '_rssi_interval', '_stable_cycles',
        '_state_change_counter', '_consecutive_failures', '_running',
        '_storm_threshold', '_max_reconnect_failures', '_raised_repair_issues',
        '_reconnect_task', '_rssi_task', '_command_semaphore',
    })
    # Attrs that need dual-write (coordinator + conn_mgr)
    _DUAL_ATTRS = frozenset({'_hass', '_entry_id'})

    def __getattr__(self, name: str) -> Any:
        # __getattr__ is only called when normal lookup fails
        if '_conn_mgr' not in self.__dict__:
            raise AttributeError(name)
        cm = self.__dict__['_conn_mgr']
        if name in self._CONN_ATTRS or hasattr(cm, name):
            return getattr(cm, name)
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        has_cm = '_conn_mgr' in self.__dict__
        if name in TuyaBLEMeshCoordinator._CONN_ATTRS and has_cm:
            setattr(self.__dict__['_conn_mgr'], name, value)
        elif name in TuyaBLEMeshCoordinator._DUAL_ATTRS and has_cm:
            super().__setattr__(name, value)
            setattr(self.__dict__['_conn_mgr'], name, value)
        else:
            super().__setattr__(name, value)

    def _classify_error(self, err: Exception) -> ErrorClass: return self._conn_mgr.classify_error(err)
    def _is_bridge_device(self) -> bool: return self._conn_mgr.is_bridge_device()
    def _adjust_polling_interval(self) -> None: self._conn_mgr.adjust_polling_interval()
    def _start_rssi_polling(self) -> None: self._conn_mgr.start_rssi_polling()
    def _stop_rssi_polling(self) -> None: self._conn_mgr.stop_rssi_polling()

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
        self, coro_func: Callable[[], Any], *,
        max_retries: int | None = None, base_delay: float | None = None,
        description: str = "command",
    ) -> None:
        await self._conn_mgr.send_command_with_retry(
            coro_func, max_retries=max_retries,
            base_delay=base_delay, description=description)

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
            self._hass.loop.call_soon_threadsafe(
                lambda: asyncio.create_task(self.async_set_updated_data(None)))
        else:
            self._notify_listeners()

    # --- ConnectionManager callbacks ---

    def _handle_reconnected(self, response_time: float) -> None:
        self._state = replace(
            self._state, available=True,
            firmware_version=self._device.firmware_version)
        self._start_rssi_polling()
        self._dispatch_update()

    def _handle_conn_state_update(self) -> None:
        ec = self._conn_mgr.statistics.last_error_class
        if ec == ErrorClass.PERMANENT.value:
            self._state = replace(
                self._state, available=False,
                device_availability=DeviceAvailabilityState.REPROVISION_REQUIRED.value,
                degraded_reason=f"Permanent error: {ec}")
        elif not self._state.available and self._conn_mgr.consecutive_failures > 0:
            avail = DeviceAvailabilityState.UNREACHABLE.value
            if ec == ErrorClass.MESH_AUTH.value:
                avail = DeviceAvailabilityState.REPROVISION_REQUIRED.value
            self._state = replace(
                self._state, available=False, device_availability=avail,
                degraded_reason=f"{ec}: {(self._conn_mgr.statistics.last_error or '')[:100]}")
        rssi = self._conn_mgr.latest_rssi
        if rssi is not None and rssi != self._state.rssi:
            self._state = replace(self._state, rssi=rssi)
        self._dispatch_update()

    # --- BLE notification callbacks ---

    def _make_notify_state(self, now: float, **fields: Any) -> TuyaBLEMeshDeviceState:
        """Build state update with standard notify fields."""
        return replace(
            self._state, available=True, last_seen=now,
            state_confidence=1.0,
            last_update_source=StateUpdateSource.NOTIFY.value,
            last_update_time=now,
            device_availability=DeviceAvailabilityState.AVAILABLE.value,
            consecutive_write_failures=0, degraded_reason=None, **fields)

    def _on_onoff_update(self, on: bool) -> None:
        was_available = self._state.available
        changed = self._state.is_on != on
        now = time.time()
        self._state = self._make_notify_state(
            now, is_on=on,
            last_confirmed_state=MappingProxyType({"is_on": on}))
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
            or self._state.red != status.red or self._state.green != status.green
            or self._state.blue != status.blue
            or self._state.color_brightness != status.color_brightness)
        is_on = status.white_brightness > 0 or status.color_brightness > 0
        confirmed = MappingProxyType({
            "is_on": is_on, "mode": status.mode,
            "brightness": status.white_brightness, "color_temp": status.white_temp,
            "red": status.red, "green": status.green, "blue": status.blue,
            "color_brightness": status.color_brightness})
        self._state = self._make_notify_state(
            now, mode=status.mode, brightness=status.white_brightness,
            color_temp=status.white_temp, red=status.red, green=status.green,
            blue=status.blue, color_brightness=status.color_brightness,
            is_on=is_on, last_confirmed_state=confirmed)
        self._conn_mgr.backoff = _INITIAL_BACKOFF
        if changed:
            self._conn_mgr.record_state_change()
        if changed or not was_available:
            self._dispatch_update()

    def _on_vendor_update(self, opcode: int, params: bytes) -> None:
        from tuya_ble_mesh.sig_mesh_protocol import (
            DP_ID_ENERGY_KWH, DP_ID_POWER_W, TUYA_CMD_TIMESTAMP_SYNC,
            TUYA_VENDOR_OPCODE, parse_tuya_vendor_frame)
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
                self._state, power_w=power_w, energy_kwh=energy_kwh, available=True,
                last_confirmed_state=MappingProxyType(cd), state_confidence=1.0,
                last_update_source=StateUpdateSource.NOTIFY.value, last_update_time=now)
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
                self._seq_persist_task = asyncio.create_task(self._save_seq())
            except RuntimeError:
                pass

    async def _load_seq(self) -> None:
        if self._hass is None or self._entry_id is None:
            return
        if not self.capabilities.has_sig_sequence:
            return
        from homeassistant.helpers.storage import Store
        self._seq_store = Store(self._hass, _SEQ_STORE_VERSION,
                                f"tuya_ble_mesh.seq.{self._entry_id}")
        data = await self._seq_store.async_load()
        if data is not None and "seq" in data:
            restored = data["seq"] + _SEQ_SAFETY_MARGIN
            self._device.set_seq(restored)
            _LOGGER.info("Restored seq=%d (stored=%d + margin=%d)",
                         restored, data["seq"], _SEQ_SAFETY_MARGIN)

    async def _save_seq(self) -> None:
        if self._seq_store is None or not self.capabilities.has_sig_sequence:
            return
        seq = self._device.get_seq()
        await self._seq_store.async_save({"seq": seq})

    # --- Lifecycle ---

    async def async_start(self) -> None:
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
                self._state, available=True,
                firmware_version=self._device.firmware_version, last_seen=time.time())
            _LOGGER.info("Coordinator started for %s (%.2fs)",
                         self._device.address, response_time)
        except Exception as err:
            self._conn_mgr.record_connection_error(err)
            _LOGGER.warning("Initial connection failed for %s, scheduling reconnect",
                            self._device.address, exc_info=True)
            self._state = replace(self._state, available=False)
            self._conn_mgr.schedule_reconnect()
        self._dispatch_update()

    async def async_stop(self) -> None:
        self._conn_mgr.running = False
        await self._save_seq()
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
        for key in ("is_on", "brightness", "color_temp", "red", "green", "blue",
                     "color_brightness", "mode"):
            if key in sent:
                updates[key] = sent[key]
        self._state = replace(self._state, **updates)
        self._dispatch_update()
