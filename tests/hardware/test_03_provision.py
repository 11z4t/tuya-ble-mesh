"""Hardware test: Provisioning (3-step AES-ECB handshake).

Verifies that MeshDevice.connect() successfully provisions the
device using the Telink mesh pairing protocol.
"""

from __future__ import annotations

import pytest
from tuya_ble_mesh.device import MeshDevice

from tests.hardware.conftest import requires_bluetooth


@requires_bluetooth
class TestProvision:
    """Test device provisioning."""

    @pytest.mark.asyncio
    async def test_connect_and_provision(
        self,
        target_mac: str,
        mesh_name: bytes,
        mesh_password: bytes,
    ) -> None:
        """Full connect + provision should succeed."""
        device = MeshDevice(target_mac, mesh_name, mesh_password)
        try:
            await device.connect(timeout=30.0)
            assert device.is_connected, "Device not connected after connect()"
        finally:
            await device.disconnect()

    @pytest.mark.asyncio
    async def test_context_manager(
        self,
        target_mac: str,
        mesh_name: bytes,
        mesh_password: bytes,
    ) -> None:
        """Async context manager should connect and disconnect cleanly."""
        device = MeshDevice(target_mac, mesh_name, mesh_password)
        async with device:
            assert device.is_connected
        assert not device.is_connected
