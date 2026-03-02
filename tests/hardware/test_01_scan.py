"""Hardware test: BLE device discovery.

Verifies that the target device is discoverable via BLE scan.
"""

from __future__ import annotations

import pytest
from tuya_ble_mesh.scanner import scan_for_tuya_devices

from tests.hardware.conftest import requires_bluetooth


@requires_bluetooth
class TestBLEScan:
    """Test BLE scanning for Tuya devices."""

    @pytest.mark.asyncio
    async def test_scan_finds_devices(self) -> None:
        """Scan should find at least one BLE device."""
        devices = await scan_for_tuya_devices(timeout=15.0)
        assert len(devices) > 0, "No Tuya mesh devices found — is the device powered on?"

    @pytest.mark.asyncio
    async def test_scan_finds_target(self, target_mac: str) -> None:
        """Scan should find the specific target device."""
        devices = await scan_for_tuya_devices(timeout=15.0)
        macs = [d.address.upper() for d in devices]
        assert target_mac.upper() in macs, f"Target {target_mac} not found. Found: {macs}"
