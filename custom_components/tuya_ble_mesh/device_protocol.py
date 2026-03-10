"""Device protocol interface for Tuya BLE Mesh devices.

Defines the protocol that all mesh devices must implement via duck-typing.
This ABC documents the expected interface without requiring inheritance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class DeviceProtocol(ABC):
    """Protocol for BLE mesh devices (ABC for duck-typing).

    All device types (MeshDevice, SIGMeshDevice, TelinkBridgeDevice,
    SIGMeshBridgeDevice) must implement this interface via duck-typing.

    Required attributes:
        address: str - Device MAC address or unique identifier
        firmware_version: str | None - Device firmware version

    Required methods:
        connect() -> None - Establish connection to device
        disconnect() -> None - Close connection
        register_disconnect_callback(cb) - Register disconnect handler

    Optional methods (device-specific):
        register_onoff_callback(cb) - For SIG Mesh GenericOnOff updates
        register_vendor_callback(cb) - For Tuya vendor messages
        register_composition_callback(cb) - For SIG Mesh composition data
        register_status_callback(cb) - For Telink status updates
        unregister_*_callback(cb) - Unregister specific callback
        set_seq(seq: int) - Set sequence number (SIG Mesh)
        get_seq() -> int - Get current sequence number (SIG Mesh)
    """

    @property
    @abstractmethod
    def address(self) -> str:
        """Return device MAC address or unique identifier."""
        ...

    @property
    @abstractmethod
    def firmware_version(self) -> str | None:
        """Return device firmware version (None if unknown)."""
        ...

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the device.

        Raises:
            Exception: Connection failed (subclasses should use custom exceptions).
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the device."""
        ...

    @abstractmethod
    def register_disconnect_callback(self, callback: Callable[[], None]) -> None:
        """Register a callback to be called when device disconnects.

        Args:
            callback: Function to call on disconnect (no arguments).
        """
        ...

    @abstractmethod
    def unregister_disconnect_callback(self, callback: Callable[[], None]) -> None:
        """Unregister a disconnect callback.

        Args:
            callback: The callback function to remove.
        """
        ...


# Optional callback protocols (device-specific)

class OnOffCallbackProtocol(ABC):
    """Protocol for devices supporting GenericOnOff updates (SIG Mesh)."""

    @abstractmethod
    def register_onoff_callback(self, callback: Callable[[bool], None]) -> None:
        """Register callback for GenericOnOff status updates.

        Args:
            callback: Function called with on/off state (True=on, False=off).
        """
        ...

    @abstractmethod
    def unregister_onoff_callback(self, callback: Callable[[bool], None]) -> None:
        """Unregister an onoff callback.

        Args:
            callback: The callback function to remove.
        """
        ...


class VendorCallbackProtocol(ABC):
    """Protocol for devices supporting Tuya vendor messages."""

    @abstractmethod
    def register_vendor_callback(self, callback: Callable[[int, bytes], None]) -> None:
        """Register callback for Tuya vendor messages.

        Args:
            callback: Function called with (opcode, params).
        """
        ...

    @abstractmethod
    def unregister_vendor_callback(self, callback: Callable[[int, bytes], None]) -> None:
        """Unregister a vendor callback.

        Args:
            callback: The callback function to remove.
        """
        ...


class CompositionCallbackProtocol(ABC):
    """Protocol for devices supporting SIG Mesh composition data."""

    @abstractmethod
    def register_composition_callback(self, callback: Callable[[object], None]) -> None:
        """Register callback for Composition Data updates.

        Args:
            callback: Function called with CompositionData object.
        """
        ...

    @abstractmethod
    def unregister_composition_callback(self, callback: Callable[[object], None]) -> None:
        """Unregister a composition callback.

        Args:
            callback: The callback function to remove.
        """
        ...


class StatusCallbackProtocol(ABC):
    """Protocol for devices supporting Telink status updates."""

    @abstractmethod
    def register_status_callback(self, callback: Callable[[object], None]) -> None:
        """Register callback for Telink status updates.

        Args:
            callback: Function called with StatusResponse object.
        """
        ...

    @abstractmethod
    def unregister_status_callback(self, callback: Callable[[object], None]) -> None:
        """Unregister a status callback.

        Args:
            callback: The callback function to remove.
        """
        ...


class SequenceProtocol(ABC):
    """Protocol for devices with sequence number management (SIG Mesh)."""

    @abstractmethod
    def set_seq(self, seq: int) -> None:
        """Set sequence number (for replay protection).

        Args:
            seq: Sequence number to set.
        """
        ...

    @abstractmethod
    def get_seq(self) -> int:
        """Get current sequence number.

        Returns:
            Current sequence number.
        """
        ...
