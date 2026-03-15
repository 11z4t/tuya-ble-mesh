"""Hardware test: Connection manager (keep-alive, reconnect, queue drain).

VERIFY: These tests exercise the BLE connection manager with real hardware.
Run with: pytest tests/hardware/test_07_connection_manager.py -v -s

Test 1: Keep-alive maintains connection > 60s
Test 2: Auto-reconnect after power cycle
Test 3: Command queue drains after reconnect
"""

from __future__ import annotations

import asyncio

import pytest
from tuya_ble_mesh.device import MeshDevice
from tuya_ble_mesh.power import BridgePowerController

from tests.hardware.conftest import SHELLY_HOST, requires_bluetooth


@requires_bluetooth
class TestKeepAlive:
    """Test keep-alive maintains connection beyond 60s timeout."""

    @pytest.mark.asyncio
    async def test_keep_alive_holds_connection_90s(
        self,
        target_mac: str,
        mesh_name: bytes,
        mesh_password: bytes,
    ) -> None:
        """Keep-alive should hold connection for > 60s.

        VERIFY: Device stays connected for full 90s without dropping.
        The device normally drops at ~60s without keep-alive.
        """
        device = MeshDevice(target_mac, mesh_name, mesh_password)
        await device.connect()
        assert device.is_connected

        print("Connected. Waiting 90s with keep-alive...")
        for elapsed in range(0, 90, 10):
            await asyncio.sleep(10.0)
            print(f"  {elapsed + 10}s elapsed — connected: {device.is_connected}")
            assert device.is_connected, f"Connection dropped at {elapsed + 10}s"

        # Send a command to prove connection is live
        await device.send_power(True)
        await asyncio.sleep(1.0)
        await device.send_power(False)
        print("VERIFY: Light flashed after 90s — keep-alive working")

        await device.disconnect()


@requires_bluetooth
class TestAutoReconnect:
    """Test auto-reconnect after power cycle."""

    @pytest.mark.asyncio
    async def test_reconnect_after_power_cycle(
        self,
        target_mac: str,
        mesh_name: bytes,
        mesh_password: bytes,
    ) -> None:
        """Power cycle device and verify auto-reconnect.

        VERIFY: Device reconnects after power cycle and command works.
        """
        device = MeshDevice(target_mac, mesh_name, mesh_password)
        shelly = BridgePowerController(SHELLY_HOST)

        def on_disconnect() -> None:
            print("  Disconnect detected!")

        device.register_disconnect_callback(on_disconnect)

        # Connect and verify
        await device.connect()
        assert device.is_connected
        await device.send_power(True)
        await asyncio.sleep(1.0)
        print("Connected and light ON. Power cycling in 3s...")
        await asyncio.sleep(3.0)

        # Power cycle
        print("  Power OFF...")
        await shelly.turn_off()
        await asyncio.sleep(5.0)  # Wait for device to fully power down

        print("  Power ON...")
        await shelly.turn_on()
        await asyncio.sleep(10.0)  # Wait for device to boot

        # Reconnect
        print("  Attempting reconnect...")
        await device.connect()
        assert device.is_connected

        # Verify command works
        await device.send_power(True)
        await asyncio.sleep(1.0)
        print("VERIFY: Light is ON after power cycle reconnect")

        await device.send_power(False)
        await device.disconnect()


@requires_bluetooth
class TestCommandQueueDrain:
    """Test command queue drains after reconnect."""

    @pytest.mark.asyncio
    async def test_queued_commands_execute_on_reconnect(
        self,
        target_mac: str,
        mesh_name: bytes,
        mesh_password: bytes,
    ) -> None:
        """Queue commands while disconnected, verify they execute on reconnect.

        VERIFY: Light turns on and brightness changes after reconnect.
        """
        device = MeshDevice(target_mac, mesh_name, mesh_password)

        # Connect initially
        await device.connect()
        assert device.is_connected
        print("Initial connection established")

        # Disconnect
        await device.disconnect()
        assert not device.is_connected
        print("Disconnected. Connection state:", device.connection.state)

        # Queue commands while disconnected
        # Note: send_command with queue awaits the future, so we need
        # to reconnect in parallel
        async def reconnect_soon() -> None:
            await asyncio.sleep(2.0)
            print("  Reconnecting...")
            await device.connect()
            print("  Reconnected! Queue should drain...")

        async def send_with_queue() -> None:
            print("  Sending power ON (will queue)...")
            await device.send_power(True)
            print("  Power ON delivered!")
            await device.send_brightness(80)
            print("  Brightness 80% delivered!")

        # Run reconnect and queued sends concurrently
        await asyncio.gather(reconnect_soon(), send_with_queue())

        await asyncio.sleep(2.0)
        print("VERIFY: Light should be ON at ~80% brightness")

        await device.send_power(False)
        await device.disconnect()
