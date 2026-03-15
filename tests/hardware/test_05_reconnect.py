"""Hardware test: Disconnect and reconnect.

Verifies that the device can reconnect after a power cycle
using the Shelly smart plug.
"""

from __future__ import annotations

import asyncio

import pytest
from tuya_ble_mesh.device import MeshDevice
from tuya_ble_mesh.power import BridgePowerController

from tests.hardware.conftest import SHELLY_HOST, requires_bluetooth


@requires_bluetooth
class TestReconnect:
    """Test device reconnection after power cycle."""

    @pytest.mark.asyncio
    async def test_reconnect_after_power_cycle(
        self,
        target_mac: str,
        mesh_name: bytes,
        mesh_password: bytes,
    ) -> None:
        """Connect, power cycle, reconnect. VERIFY: light comes back."""
        # First connection
        device = MeshDevice(target_mac, mesh_name, mesh_password)
        await device.connect(timeout=30.0)
        assert device.is_connected
        await device.send_power(True)
        print("VERIFY: Light is ON (before power cycle)")
        await device.disconnect()

        # Power cycle via Shelly
        shelly = BridgePowerController(SHELLY_HOST)
        await shelly.power_cycle(off_seconds=3.0)
        await shelly.close()

        # Wait for device to boot
        await asyncio.sleep(10.0)

        # Reconnect
        device2 = MeshDevice(target_mac, mesh_name, mesh_password)
        await device2.connect(timeout=30.0)
        assert device2.is_connected
        await device2.send_power(True)
        print("VERIFY: Light is ON (after power cycle)")
        await device2.disconnect()
