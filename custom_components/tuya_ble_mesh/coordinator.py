"""Push-based coordinator for Tuya BLE Mesh devices.

NOT a DataUpdateCoordinator subclass. BLE notifications drive state
updates via _on_status_update → listener dispatch. Reconnection uses
exponential backoff, triggered by MeshDevice disconnect callbacks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Union

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store
    from tuya_ble_mesh.device import MeshDevice  # type: ignore[import-not-found]
    from tuya_ble_mesh.protocol import StatusResponse  # type: ignore[import-not-found]
    from tuya_ble_mesh.sig_mesh_bridge import SIGMeshBridgeDevice, TelinkBridgeDevice  # type: ignore[import-not-found]
    from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice  # type: ignore[import-not-found]
    from tuya_ble_mesh.sig_mesh_protocol import CompositionData  # type: ignore[import-not-found]

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

# Sequence number persistence
_SEQ_PERSIST_INTERVAL = 10  # Save seq every N commands
_SEQ_SAFETY_MARGIN = 100  # Add margin on restore to avoid replay
_SEQ_STORE_VERSION = 1


@dataclass
class TuyaBLEMeshDeviceState:
    """Current state of a Tuya BLE Mesh device."""

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
    connection_uptime: float = 0.0  # Total seconds connected
    last_disconnect_time: float | None = None
    avg_response_time: float = 0.0  # Average response time in seconds


class TuyaBLEMeshCoordinator:
    """Push-based coordinator for a single BLE mesh device.

    Receives BLE notifications and dispatches state to HA entities.
    Handles reconnection with exponential backoff, triggered by
    MeshDevice disconnect callbacks (not polling).
    """

    def __init__(
        self,
        device: AnyMeshDevice,
        *,
        hass: HomeAssistant | None = None,
        entry_id: str | None = None,
    ) -> None:
        self._device: AnyMeshDevice = device
        self._state = TuyaBLEMeshDeviceState()
        self._listeners: list[Callable[[], None]] = []
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

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Register a state change listener.

        Args:
            callback: Called when device state changes.

        Returns:
            A function to remove the listener.
        """
        self._listeners.append(callback)

        def remove() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return remove

    def _notify_listeners(self) -> None:
        """Notify all registered listeners of state change."""
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:
                _LOGGER.warning("Listener callback error", exc_info=True)

    def _on_onoff_update(self, on: bool) -> None:
        """Handle a GenericOnOff Status from a SIG Mesh device.

        Args:
            on: True if device is on, False if off.
        """
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
            self._seq_persist_task = asyncio.ensure_future(self._save_seq())

        _LOGGER.debug("OnOff update: on=%s (changed=%s)", on, changed)
        self._notify_listeners()

    def _on_status_update(self, status: StatusResponse) -> None:
        """Handle a status notification from the device.

        Args:
            status: Decoded status from BLE notification.
        """
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

        self._notify_listeners()

    def _on_vendor_update(self, opcode: int, params: bytes) -> None:
        """Handle a Tuya vendor message from a SIG Mesh device.

        Parses vendor DPs for power/energy data.

        Args:
            opcode: 3-byte vendor opcode.
            params: Raw vendor message parameters.
        """
        from tuya_ble_mesh.sig_mesh_protocol import (  # type: ignore[import-not-found]
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
            self._notify_listeners()

    def _on_composition_update(self, comp: CompositionData) -> None:
        """Handle a Composition Data response from a SIG Mesh device.

        Updates firmware_version in state from device's firmware_version property.

        Args:
            comp: Parsed composition data.
        """
        self._state.firmware_version = self._device.firmware_version
        _LOGGER.debug("Composition Data received, firmware=%s", self._state.firmware_version)
        self._notify_listeners()

    def _on_disconnect(self) -> None:
        """Handle device disconnect — mark unavailable and schedule reconnect.

        Called by MeshDevice disconnect callback when the BLE connection
        is lost (write failure or keep-alive timeout).
        """
        _LOGGER.warning("Device disconnected: %s", self._device.address)

        # Update connection statistics
        if self._stats.connect_time is not None:
            uptime = time.time() - self._stats.connect_time
            self._stats.connection_uptime += uptime
        self._stats.last_disconnect_time = time.time()

        self._state.available = False
        self._stop_rssi_polling()
        self._notify_listeners()
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

        self._notify_listeners()

    async def async_stop(self) -> None:
        """Stop the coordinator — disconnect and cancel reconnection."""
        self._running = False

        # Persist seq before stopping
        await self._save_seq()

        self._stop_rssi_polling()

        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None

        if hasattr(self._device, "unregister_onoff_callback"):
            self._device.unregister_onoff_callback(self._on_onoff_update)
        if hasattr(self._device, "unregister_vendor_callback"):
            self._device.unregister_vendor_callback(self._on_vendor_update)
        if hasattr(self._device, "unregister_composition_callback"):
            self._device.unregister_composition_callback(self._on_composition_update)
        if hasattr(self._device, "unregister_status_callback"):
            self._device.unregister_status_callback(self._on_status_update)
        self._device.unregister_disconnect_callback(self._on_disconnect)

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

        self._reconnect_task = asyncio.ensure_future(self._reconnect_loop())

    async def _reconnect_loop(self) -> None:
        """Attempt reconnection with exponential backoff."""
        while self._running:
            _LOGGER.info(
                "Reconnecting to %s in %.0fs",
                self._device.address,
                self._backoff,
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
                self._state.available = True
                self._state.firmware_version = self._device.firmware_version
                self._backoff = _INITIAL_BACKOFF
                self._start_rssi_polling()
                _LOGGER.info("Reconnected to %s (%.2fs)", self._device.address, response_time)
                self._notify_listeners()
                return
            except Exception as err:
                self._stats.total_errors += 1
                self._stats.connection_errors += 1
                self._stats.last_error = str(err)
                self._stats.last_error_time = time.time()
                _LOGGER.warning(
                    "Reconnect failed for %s",
                    self._device.address,
                    exc_info=True,
                )
                self._state.available = False
                self._backoff = min(self._backoff * _BACKOFF_MULTIPLIER, _MAX_BACKOFF)
                self._notify_listeners()

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
        self._rssi_task = asyncio.ensure_future(self._rssi_loop())

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
                        self._notify_listeners()

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
