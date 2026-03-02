"""Hardware test: GATT connection and service enumeration.

Verifies that we can connect to the target device and enumerate
its GATT services (expecting Telink mesh UUIDs).
"""

from __future__ import annotations

import pytest
from bleak import BleakClient
from tuya_ble_mesh.const import TELINK_BASE_UUID_PREFIX

from tests.hardware.conftest import requires_bluetooth


@requires_bluetooth
class TestBLEConnect:
    """Test BLE GATT connection."""

    @pytest.mark.asyncio
    async def test_connect_and_enumerate_services(self, target_mac: str) -> None:
        """Connect to device and verify Telink service is present."""
        async with BleakClient(target_mac, timeout=30.0) as client:
            assert client.is_connected
            services = client.services
            service_uuids = [str(s.uuid) for s in services]
            telink_services = [
                u for u in service_uuids if u.startswith(TELINK_BASE_UUID_PREFIX[:8])
            ]
            assert len(telink_services) > 0, f"No Telink services found. Services: {service_uuids}"

    @pytest.mark.asyncio
    async def test_connect_finds_command_characteristic(self, target_mac: str) -> None:
        """Connect and verify command characteristic (1912) exists."""
        from tuya_ble_mesh.const import TELINK_CHAR_COMMAND

        async with BleakClient(target_mac, timeout=30.0) as client:
            char = client.services.get_characteristic(TELINK_CHAR_COMMAND)
            assert char is not None, f"Command characteristic {TELINK_CHAR_COMMAND} not found"
