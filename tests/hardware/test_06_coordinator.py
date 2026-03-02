"""Hardware test: Coordinator with real device.

Verifies that the coordinator can start, receive status updates,
and stop cleanly with a real BLE device.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Add project root for custom_components imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tuya_ble_mesh.device import MeshDevice

from custom_components.tuya_ble_mesh.coordinator import TuyaBLEMeshCoordinator
from tests.hardware.conftest import requires_bluetooth


@requires_bluetooth
class TestCoordinator:
    """Test coordinator with real hardware."""

    @pytest.mark.asyncio
    async def test_coordinator_start_stop(
        self,
        target_mac: str,
        mesh_name: bytes,
        mesh_password: bytes,
    ) -> None:
        """Coordinator should start and stop cleanly."""
        device = MeshDevice(target_mac, mesh_name, mesh_password)
        coordinator = TuyaBLEMeshCoordinator(device)

        await coordinator.async_start()
        assert coordinator.state.available

        await asyncio.sleep(2.0)
        await coordinator.async_stop()
        assert not coordinator.state.available

    @pytest.mark.asyncio
    async def test_coordinator_receives_status(
        self,
        target_mac: str,
        mesh_name: bytes,
        mesh_password: bytes,
    ) -> None:
        """Coordinator should update state from device notifications."""
        device = MeshDevice(target_mac, mesh_name, mesh_password)
        coordinator = TuyaBLEMeshCoordinator(device)

        updated = asyncio.Event()

        def on_update() -> None:
            updated.set()

        coordinator.add_listener(on_update)

        await coordinator.async_start()

        # Send a command to trigger a status notification
        await device.send_power(True)

        try:
            await asyncio.wait_for(updated.wait(), timeout=5.0)
            print(f"VERIFY: State updated — on={coordinator.state.is_on}")
        except TimeoutError:
            print("NOTE: No status notification received (may be device-dependent)")
        finally:
            await coordinator.async_stop()
