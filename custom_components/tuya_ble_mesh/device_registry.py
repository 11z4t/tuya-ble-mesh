"""Persistent device registry for Tuya BLE Mesh.

Tracks device metadata beyond what HA's built-in device registry stores:
- First seen / last seen timestamps
- Connection and error history
- RSSI history (rolling window)
- Device capabilities discovered at runtime

Data is persisted in HA Store so it survives restarts.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

_STORE_KEY = "tuya_ble_mesh_device_registry"
_STORE_VERSION = 1

# Rolling RSSI history: keep last N readings
_RSSI_HISTORY_MAXLEN = 50


@dataclass
class DeviceMetadata:
    """Metadata for a single Tuya BLE Mesh device.

    Stores connection history, performance statistics and capabilities.
    All timestamps are Unix timestamps (float, seconds since epoch).
    """

    address: str
    name: str
    device_type: str

    # Discovery timestamps
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    # Connection metrics
    connection_count: int = 0
    error_count: int = 0
    last_error: str | None = None
    last_error_time: float | None = None

    # RSSI history (in-memory only, not persisted)
    rssi_history: deque[int] = field(
        default_factory=lambda: deque(maxlen=_RSSI_HISTORY_MAXLEN),
        compare=False,
        repr=False,
    )

    # Firmware version discovered at runtime
    firmware_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for persistence (excludes in-memory fields).

        Returns:
            Dict safe for JSON serialization.
        """
        return {
            "address": self.address,
            "name": self.name,
            "device_type": self.device_type,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "connection_count": self.connection_count,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "last_error_time": self.last_error_time,
            "firmware_version": self.firmware_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceMetadata:
        """Deserialize from a persisted dict.

        Args:
            data: Dict from storage (as saved by to_dict()).

        Returns:
            DeviceMetadata instance with restored values.
        """
        return cls(
            address=data["address"],
            name=data["name"],
            device_type=data["device_type"],
            first_seen=data.get("first_seen", time.time()),
            last_seen=data.get("last_seen", time.time()),
            connection_count=data.get("connection_count", 0),
            error_count=data.get("error_count", 0),
            last_error=data.get("last_error"),
            last_error_time=data.get("last_error_time"),
            firmware_version=data.get("firmware_version"),
        )

    @property
    def avg_rssi(self) -> float | None:
        """Return average RSSI from recent history, or None if no data.

        Returns:
            Mean RSSI (dBm) or None.
        """
        if not self.rssi_history:
            return None
        return sum(self.rssi_history) / len(self.rssi_history)

    @property
    def uptime_fraction(self) -> float:
        """Return fraction of time device was connected (connections / (connections + errors)).

        Returns:
            Float 0.0–1.0. Returns 1.0 if no events recorded.
        """
        total = self.connection_count + self.error_count
        if total == 0:
            return 1.0
        return self.connection_count / total


class TuyaBLEMeshDeviceRegistry:
    """Registry for Tuya BLE Mesh device metadata.

    Provides:
    - Per-device metadata storage (first/last seen, connection history)
    - Persistence across restarts via HA Store
    - In-memory RSSI history (not persisted)
    - Lookup by MAC address

    Usage::

        registry = TuyaBLEMeshDeviceRegistry(hass)
        await registry.async_load()
        registry.register_device("DC:23:4F:10:52:C4", "My Light", "light")
        registry.record_connection("DC:23:4F:10:52:C4")
        await registry.async_save()
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the registry.

        Args:
            hass: Home Assistant instance (used for HA Store).
        """
        self._hass = hass
        self._devices: dict[str, DeviceMetadata] = {}
        self._store: Store[dict[str, Any]] | None = None

    async def async_load(self) -> None:
        """Load persisted device data from HA Store.

        Should be called once during integration setup.
        """
        from homeassistant.helpers.storage import Store

        self._store = Store(self._hass, _STORE_VERSION, _STORE_KEY)
        data = await self._store.async_load()
        if data and isinstance(data, dict):
            for addr, device_data in data.items():
                try:
                    self._devices[addr.upper()] = DeviceMetadata.from_dict(device_data)
                except (KeyError, TypeError) as exc:
                    _LOGGER.warning("Could not restore device registry entry %s: %s", addr, exc)

        _LOGGER.debug("Device registry loaded: %d devices", len(self._devices))

    async def async_save(self) -> None:
        """Persist device data to HA Store.

        No-op if store is not initialized (async_load not called).
        """
        if self._store is None:
            return
        data = {addr: meta.to_dict() for addr, meta in self._devices.items()}
        await self._store.async_save(data)
        _LOGGER.debug("Device registry saved: %d devices", len(self._devices))

    def register_device(self, address: str, name: str, device_type: str) -> DeviceMetadata:
        """Register a device or update its name/type if already known.

        Does NOT update last_seen — call record_connection() for that.

        Args:
            address: BLE MAC address (case-insensitive).
            name: Human-readable device name.
            device_type: Device type string (e.g. ``"light"``).

        Returns:
            The DeviceMetadata entry (new or existing).
        """
        key = address.upper()
        if key not in self._devices:
            self._devices[key] = DeviceMetadata(
                address=key,
                name=name,
                device_type=device_type,
            )
            _LOGGER.info("Registered new device in registry: %s (%s)", key, device_type)
        else:
            # Update mutable fields only
            entry = self._devices[key]
            entry.name = name
            entry.device_type = device_type
        return self._devices[key]

    def get_device(self, address: str) -> DeviceMetadata | None:
        """Look up device metadata by MAC address.

        Args:
            address: BLE MAC address (case-insensitive).

        Returns:
            DeviceMetadata if found, None otherwise.
        """
        return self._devices.get(address.upper())

    def get_all_devices(self) -> list[DeviceMetadata]:
        """Return all registered devices sorted by last_seen (most recent first).

        Returns:
            List of DeviceMetadata objects.
        """
        return sorted(self._devices.values(), key=lambda d: d.last_seen, reverse=True)

    def record_connection(self, address: str) -> None:
        """Record a successful connection for a device.

        Updates last_seen and increments connection_count.

        Args:
            address: BLE MAC address.
        """
        entry = self._devices.get(address.upper())
        if entry is None:
            return
        entry.last_seen = time.time()
        entry.connection_count += 1

    def record_error(self, address: str, error: str) -> None:
        """Record a connection or communication error.

        Args:
            address: BLE MAC address.
            error: Short error description (must NOT contain secrets).
        """
        entry = self._devices.get(address.upper())
        if entry is None:
            return
        entry.error_count += 1
        entry.last_error = error
        entry.last_error_time = time.time()

    def record_rssi(self, address: str, rssi: int) -> None:
        """Append an RSSI reading to the rolling history.

        Args:
            address: BLE MAC address.
            rssi: Signal strength in dBm (typically -100 to 0).
        """
        entry = self._devices.get(address.upper())
        if entry is None:
            return
        entry.rssi_history.append(rssi)

    def update_firmware_version(self, address: str, version: str) -> None:
        """Update the firmware version for a device.

        Args:
            address: BLE MAC address.
            version: Firmware version string.
        """
        entry = self._devices.get(address.upper())
        if entry is None:
            return
        entry.firmware_version = version

    def remove_device(self, address: str) -> bool:
        """Remove a device from the registry.

        Args:
            address: BLE MAC address.

        Returns:
            True if device was found and removed, False if not found.
        """
        key = address.upper()
        if key in self._devices:
            del self._devices[key]
            _LOGGER.info("Removed device from registry: %s", key)
            return True
        return False
