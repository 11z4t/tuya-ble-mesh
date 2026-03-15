"""Common device interface protocol for BLE mesh devices.

Defines a Protocol class that all device implementations (MeshDevice,
SIGMeshDevice, BLEConnection, SIGMeshBridgeDevice, TelinkBridgeDevice)
conform to. This allows duck-typing across different transport mechanisms
while maintaining type safety with mypy and pyright.

 Extracted from device.py, sig_mesh_device.py, connection.py,
and sig_mesh_bridge.py to eliminate code duplication and provide a
single source of truth for the common device interface.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tuya_ble_mesh.const import (
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_MAX_RETRIES,
)


@runtime_checkable
class MeshDeviceProtocol(Protocol):
    """Protocol for all BLE mesh device implementations.

    This protocol defines the minimal interface that all device classes
    must implement, regardless of transport mechanism (direct BLE,
    SIG Mesh, or bridge HTTP).

    All implementations must provide:
    - address: BLE MAC address (uppercase, e.g. "DC:23:4D:21:43:A5")
    - is_connected: Connection state check
    - firmware_version: Firmware version string or None
    - connect(): Async connection with timeout and retry
    - disconnect(): Async disconnection and cleanup

    Implementations:
    - MeshDevice (device.py): Telink direct BLE
    - SIGMeshDevice (sig_mesh_device.py): SIG Mesh GATT Proxy
    - BLEConnection (connection.py): Low-level BLE transport
    - SIGMeshBridgeDevice (sig_mesh_bridge.py): SIG Mesh via HTTP bridge
    - TelinkBridgeDevice (sig_mesh_bridge.py): Telink via HTTP bridge
    """

    @property
    def address(self) -> str:
        """Return the device BLE MAC address.

        Returns:
            str: Device BLE MAC address in uppercase format (e.g. "DC:23:4D:21:43:A5").
        """
        ...

    @property
    def is_connected(self) -> bool:
        """Return True if the device is connected and ready.

        For BLEConnection, this checks if the connection is in READY state
        (use ``is_ready`` property on BLEConnection for direct access).
        For all other implementations, checks if the BLE or HTTP connection
        is established and usable.

        Returns:
            bool: True if device is connected and ready for commands.
        """
        ...

    @property
    def firmware_version(self) -> str | None:
        """Return the device firmware version, or None if not read.

        For bridge devices, may return a synthetic version string
        (e.g. "bridge", "bridge-telink") indicating bridge mode.

        Returns:
            str | None: Device firmware version, or None if not yet read.
        """
        ...

    async def connect(
        self,
        timeout: float = DEFAULT_CONNECTION_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        """Connect to the device with timeout and retry.

        For direct BLE devices, performs BLE connection and provisioning/pairing.
        For bridge devices, verifies HTTP bridge daemon is reachable.

        Args:
            timeout: Connection timeout per attempt in seconds.
            max_retries: Maximum number of connection attempts.

        Raises:
            ConnectionError: If connection fails after all retries.
            ProvisioningError: If BLE provisioning fails (direct BLE only).
            SIGMeshKeyError: If key loading fails (SIG Mesh only).
        """
        ...

    async def disconnect(self) -> None:
        """Disconnect from the device and clean up resources.

        For direct BLE devices, disconnects BLE connection and zeros key material.
        For bridge devices, closes HTTP session and marks as disconnected.

        This method should be idempotent and safe to call multiple times.
        """
        ...
