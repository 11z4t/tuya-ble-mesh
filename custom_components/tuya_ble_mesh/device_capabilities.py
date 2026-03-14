"""Device capability inspection for Tuya BLE Mesh devices.

Consolidates all ``hasattr(device, ...)`` checks into a single frozen dataclass
built at coordinator initialisation.  Consumers read ``coordinator.capabilities``
instead of performing ad-hoc attribute probing.

If the lib ever renames a method the breakage is localised here rather than
scattered across coordinator, sensor, and diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeviceCapabilities:
    """Immutable snapshot of what a mesh device instance supports.

    Attributes:
        has_status_callback: True for Tuya BLE devices (MeshDevice,
            TelinkBridgeDevice) that push state via ``register_status_callback``.
        has_onoff_callback: True for SIG Mesh devices
            (SIGMeshDevice, SIGMeshBridgeDevice).
        has_vendor_callback: True for SIG Mesh devices.
        has_composition_callback: True for SIG Mesh devices.
        has_sig_sequence: True when the device exposes ``set_seq``/``get_seq``
            for sequence-number persistence (SIG Mesh direct only).
        has_light_control: True when ``device.send_brightness`` exists,
            indicating full light-control support (brightness/colour/temp).
        has_power_monitoring: True when the device reports
            ``supports_power_monitoring=True``.
        protocol: Human-readable protocol label — ``"SIG_Mesh"`` or
            ``"Tuya_BLE"``.
    """

    has_status_callback: bool
    has_onoff_callback: bool
    has_vendor_callback: bool
    has_composition_callback: bool
    has_sig_sequence: bool
    has_light_control: bool
    has_power_monitoring: bool
    protocol: str  # "SIG_Mesh" | "Tuya_BLE"

    @classmethod
    def from_device(cls, device: Any) -> DeviceCapabilities:
        """Build capabilities by probing a device instance once.

        This is the **single authoritative place** where ``hasattr`` checks
        occur.  All other code reads from ``coordinator.capabilities``.

        Args:
            device: Any ``AnyMeshDevice`` instance (MeshDevice,
                SIGMeshDevice, TelinkBridgeDevice, SIGMeshBridgeDevice).

        Returns:
            A frozen :class:`DeviceCapabilities` for this device.
        """
        return cls(
            has_status_callback=hasattr(device, "register_status_callback"),
            has_onoff_callback=hasattr(device, "register_onoff_callback"),
            has_vendor_callback=hasattr(device, "register_vendor_callback"),
            has_composition_callback=hasattr(device, "register_composition_callback"),
            has_sig_sequence=hasattr(device, "set_seq") and hasattr(device, "get_seq"),
            has_light_control=hasattr(device, "send_brightness"),
            has_power_monitoring=bool(getattr(device, "supports_power_monitoring", False)),
            protocol="SIG_Mesh" if hasattr(device, "set_seq") else "Tuya_BLE",
        )
