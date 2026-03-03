"""Hardware test: Device commands (power, brightness, color temp).

VERIFY: These tests send real commands to the device. Visually
confirm that the light responds as expected.
"""

from __future__ import annotations

import asyncio

import pytest
from tuya_ble_mesh.device import MeshDevice

from tests.hardware.conftest import requires_bluetooth


@requires_bluetooth
class TestCommands:
    """Test sending commands to a real device."""

    @pytest.mark.asyncio
    async def test_power_on(
        self,
        target_mac: str,
        mesh_name: bytes,
        mesh_password: bytes,
    ) -> None:
        """Send power ON command. VERIFY: light turns on."""
        async with MeshDevice(target_mac, mesh_name, mesh_password) as device:
            await device.send_power(True)
            await asyncio.sleep(1.0)
            print("VERIFY: Light should be ON")

    @pytest.mark.asyncio
    async def test_power_off(
        self,
        target_mac: str,
        mesh_name: bytes,
        mesh_password: bytes,
    ) -> None:
        """Send power OFF command. VERIFY: light turns off."""
        async with MeshDevice(target_mac, mesh_name, mesh_password) as device:
            await device.send_power(True)
            await asyncio.sleep(1.0)
            await device.send_power(False)
            await asyncio.sleep(1.0)
            print("VERIFY: Light should be OFF")

    @pytest.mark.asyncio
    async def test_brightness_levels(
        self,
        target_mac: str,
        mesh_name: bytes,
        mesh_password: bytes,
    ) -> None:
        """Cycle through brightness levels. VERIFY: visible dimming."""
        async with MeshDevice(target_mac, mesh_name, mesh_password) as device:
            await device.send_power(True)
            await asyncio.sleep(0.5)

            for level in [10, 50, 100]:
                await device.send_brightness(level)
                await asyncio.sleep(1.5)
                print(f"VERIFY: Brightness should be ~{level}%")

    @pytest.mark.asyncio
    async def test_color_temp(
        self,
        target_mac: str,
        mesh_name: bytes,
        mesh_password: bytes,
    ) -> None:
        """Cycle through color temperatures. VERIFY: visible change."""
        async with MeshDevice(target_mac, mesh_name, mesh_password) as device:
            await device.send_power(True)
            await device.send_brightness(100)
            await asyncio.sleep(0.5)

            for temp in [0, 64, 127]:
                await device.send_color_temp(temp)
                await asyncio.sleep(1.5)
                label = {0: "warm", 64: "neutral", 127: "cool"}[temp]
                print(f"VERIFY: Color temp should be {label} ({temp}/127)")
