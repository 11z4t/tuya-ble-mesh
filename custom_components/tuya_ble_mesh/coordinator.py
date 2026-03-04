"""Push-based coordinator for Tuya BLE Mesh devices.

NOT a DataUpdateCoordinator subclass. BLE notifications drive state
updates via _on_status_update → listener dispatch. Reconnection uses
exponential backoff, triggered by MeshDevice disconnect callbacks.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tuya_ble_mesh.device import MeshDevice
    from tuya_ble_mesh.protocol import StatusResponse
    from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice

# RSSI refresh interval (seconds)
_RSSI_REFRESH_INTERVAL = 60.0

_LOGGER = logging.getLogger(__name__)

# Reconnect backoff parameters
_INITIAL_BACKOFF = 5.0
_MAX_BACKOFF = 300.0
_BACKOFF_MULTIPLIER = 2.0


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
    available: bool = False


class TuyaBLEMeshCoordinator:
    """Push-based coordinator for a single BLE mesh device.

    Receives BLE notifications and dispatches state to HA entities.
    Handles reconnection with exponential backoff, triggered by
    MeshDevice disconnect callbacks (not polling).
    """

    def __init__(self, device: MeshDevice | SIGMeshDevice) -> None:
        self._device: Any = device
        self._state = TuyaBLEMeshDeviceState()
        self._listeners: list[Callable[[], None]] = []
        self._reconnect_task: asyncio.Task[None] | None = None
        self._rssi_task: asyncio.Task[None] | None = None
        self._backoff = _INITIAL_BACKOFF
        self._running = False

    @property
    def device(self) -> Any:
        """Return the underlying mesh device (MeshDevice or SIGMeshDevice)."""
        return self._device

    @property
    def state(self) -> TuyaBLEMeshDeviceState:
        """Return the current device state."""
        return self._state

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
        for callback in self._listeners:
            try:
                callback()
            except Exception:
                _LOGGER.warning("Listener callback error", exc_info=True)

    def _on_onoff_update(self, on: bool) -> None:
        """Handle a GenericOnOff Status from a SIG Mesh device.

        Args:
            on: True if device is on, False if off.
        """
        self._state.is_on = on
        self._state.available = True
        self._backoff = _INITIAL_BACKOFF

        _LOGGER.debug("OnOff update: on=%s", on)
        self._notify_listeners()

    def _on_status_update(self, status: StatusResponse) -> None:
        """Handle a status notification from the device.

        Args:
            status: Decoded status from BLE notification.
        """
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

        _LOGGER.debug(
            "Status update: on=%s mode=%d bright=%d temp=%d rgb=(%d,%d,%d) cbright=%d",
            self._state.is_on,
            self._state.mode,
            self._state.brightness,
            self._state.color_temp,
            self._state.red,
            self._state.green,
            self._state.blue,
            self._state.color_brightness,
        )

        self._notify_listeners()

    def _on_disconnect(self) -> None:
        """Handle device disconnect — mark unavailable and schedule reconnect.

        Called by MeshDevice disconnect callback when the BLE connection
        is lost (write failure or keep-alive timeout).
        """
        _LOGGER.warning("Device disconnected: %s", self._device.address)
        self._state.available = False
        self._stop_rssi_polling()
        self._notify_listeners()
        self._schedule_reconnect()

    async def async_start(self) -> None:
        """Start the coordinator — connect and begin receiving notifications."""
        self._running = True

        # Wire callbacks based on device type (duck-typing)
        if hasattr(self._device, "register_onoff_callback"):
            self._device.register_onoff_callback(self._on_onoff_update)
        if hasattr(self._device, "register_status_callback"):
            self._device.register_status_callback(self._on_status_update)
        self._device.register_disconnect_callback(self._on_disconnect)

        try:
            await self._device.connect()
            self._state.available = True
            self._state.firmware_version = self._device.firmware_version
            self._backoff = _INITIAL_BACKOFF
            self._start_rssi_polling()
            _LOGGER.info("Coordinator started for %s", self._device.address)
        except Exception:
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

        self._stop_rssi_polling()

        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
            self._reconnect_task = None

        if hasattr(self._device, "unregister_onoff_callback"):
            self._device.unregister_onoff_callback(self._on_onoff_update)
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
                await self._device.connect()
                self._state.available = True
                self._state.firmware_version = self._device.firmware_version
                self._backoff = _INITIAL_BACKOFF
                self._start_rssi_polling()
                _LOGGER.info("Reconnected to %s", self._device.address)
                self._notify_listeners()
                return
            except Exception:
                _LOGGER.warning(
                    "Reconnect failed for %s",
                    self._device.address,
                    exc_info=True,
                )
                self._state.available = False
                self._backoff = min(self._backoff * _BACKOFF_MULTIPLIER, _MAX_BACKOFF)
                self._notify_listeners()

    # --- RSSI polling ---

    def _start_rssi_polling(self) -> None:
        """Start periodic RSSI refresh via BLE scan."""
        self._stop_rssi_polling()
        self._rssi_task = asyncio.ensure_future(self._rssi_loop())

    def _stop_rssi_polling(self) -> None:
        """Stop RSSI polling."""
        if self._rssi_task is not None:
            self._rssi_task.cancel()
            self._rssi_task = None

    async def _rssi_loop(self) -> None:
        """Periodically scan for device to update RSSI."""
        from bleak import BleakScanner

        try:
            while self._running and self._state.available:
                await asyncio.sleep(_RSSI_REFRESH_INTERVAL)
                if not self._running or not self._state.available:
                    break
                try:
                    device = await BleakScanner.find_device_by_address(
                        self._device.address, timeout=10.0
                    )
                    if device is not None and device.rssi is not None:
                        self._state.rssi = device.rssi
                        self._notify_listeners()
                except Exception:
                    _LOGGER.debug("RSSI scan failed (ignored)", exc_info=True)
        except asyncio.CancelledError:
            pass
