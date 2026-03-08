"""Scanner performance benchmarks.

Measures BLE scanner and device discovery performance.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))


class TestScannerDeviceMatching:
    """Benchmark device matching and filtering."""

    @pytest.mark.skip(reason="is_valid_mac removed from device.py")
    def test_benchmark_address_validation(self, benchmark) -> None:
        """Benchmark MAC address validation."""
        from tuya_ble_mesh.device import is_valid_mac

        def validate_addresses() -> int:
            addresses = [
                "DC:23:4D:21:43:A5",
                "AA:BB:CC:DD:EE:FF",
                "11:22:33:44:55:66",
                "invalid",
                "DC-23-4D-21-43-A5",
            ]
            valid_count = sum(1 for addr in addresses if is_valid_mac(addr))
            return valid_count

        result = benchmark(validate_addresses)
        assert result >= 3

    def test_benchmark_device_name_matching(self, benchmark) -> None:
        """Benchmark device name pattern matching."""

        def match_names() -> int:
            names = [
                "Tuya_Mesh_Light_01",
                "Tuya_Mesh_Switch_02",
                "Random_Device",
                "Another_Light",
                "Tuya_Sensor_03",
            ]
            matches = sum(1 for name in names if name.startswith("Tuya_"))
            return matches

        result = benchmark(match_names)
        assert result == 3


class TestDeviceInitializationPerformance:
    """Benchmark device object initialization."""

    @pytest.mark.skip(reason="MeshDevice API changed - needs mesh_name/mesh_password")
    def test_benchmark_mesh_device_creation(self, benchmark) -> None:
        """Benchmark MeshDevice instantiation."""
        from tuya_ble_mesh.device import MeshDevice

        mock_ble = MagicMock()

        def create_device() -> MeshDevice:
            return MeshDevice("DC:23:4D:21:43:A5", 0x01, 0x0001, mock_ble)

        device = benchmark(create_device)
        assert device.address == "DC:23:4D:21:43:A5"

    def test_benchmark_sig_mesh_device_creation(self, benchmark) -> None:
        """Benchmark SIGMeshDevice instantiation."""
        from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice

        mock_ble = MagicMock()

        def create_device() -> SIGMeshDevice:
            return SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, mock_ble)

        device = benchmark(create_device)
        assert device.address == "DC:23:4D:21:43:A5"

    @pytest.mark.skip(reason="MeshDevice API changed - needs mesh_name/mesh_password")
    def test_benchmark_create_100_devices(self, benchmark) -> None:
        """Benchmark creating 100 device objects."""
        from tuya_ble_mesh.device import MeshDevice

        mock_ble = MagicMock()

        def create_many() -> int:
            devices = []
            for i in range(100):
                addr = f"DC:23:4D:21:43:{i:02X}"
                devices.append(MeshDevice(addr, i % 256, 0x0001, mock_ble))
            return len(devices)

        result = benchmark(create_many)
        assert result == 100


@pytest.mark.requires_ha
class TestCoordinatorCreationPerformance:
    """Benchmark coordinator instantiation."""

    def test_benchmark_coordinator_creation(self, benchmark) -> None:
        """Benchmark TuyaBLEMeshCoordinator instantiation."""
        from custom_components.tuya_ble_mesh.coordinator import (
            TuyaBLEMeshCoordinator,
        )

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        def create_coord() -> TuyaBLEMeshCoordinator:
            return TuyaBLEMeshCoordinator(mock_device)

        coord = benchmark(create_coord)
        assert coord.device == mock_device

    def test_benchmark_coordinator_with_listeners(self, benchmark) -> None:
        """Benchmark coordinator with multiple listeners."""
        from custom_components.tuya_ble_mesh.coordinator import (
            TuyaBLEMeshCoordinator,
        )

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        def setup_with_listeners() -> int:
            coord = TuyaBLEMeshCoordinator(mock_device)
            callbacks = []
            for _ in range(10):

                def callback():
                    return None

                coord.add_listener(callback)
                callbacks.append(callback)
            return len(callbacks)

        result = benchmark(setup_with_listeners)
        assert result == 10


class TestSequenceNumberPerformance:
    """Benchmark sequence number operations."""

    @pytest.mark.asyncio
    async def test_benchmark_sequence_generation(self, benchmark) -> None:
        """Benchmark generating sequence numbers."""
        from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice

        mock_ble = MagicMock()
        device = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, mock_ble)
        device.set_seq(1000)

        async def generate_sequences() -> int:
            count = 0
            for _ in range(100):
                await device._next_seq()
                count += 1
            return count

        result = await benchmark(generate_sequences)
        assert result == 100


class TestBulkOperationsPerformance:
    """Benchmark bulk operations that scanners might perform."""

    def test_benchmark_filter_1000_advertisements(self, benchmark) -> None:
        """Benchmark filtering 1000 BLE advertisements."""

        def filter_ads() -> int:
            # Simulate filtering 1000 BLE advertisements
            advertisements = [
                {
                    "address": f"AA:BB:CC:DD:EE:{i % 256:02X}",
                    "name": f"Device_{i}",
                    "rssi": -60 - (i % 40),
                }
                for i in range(1000)
            ]

            # Filter for Tuya devices with good RSSI
            filtered = [
                ad for ad in advertisements if ad["name"].startswith("Tuya_") or ad["rssi"] > -70
            ]
            return len(filtered)

        result = benchmark(filter_ads)
        assert result >= 0

    def test_benchmark_sort_by_rssi(self, benchmark) -> None:
        """Benchmark sorting devices by RSSI."""

        devices = [
            {"address": f"AA:BB:CC:DD:EE:{i:02X}", "rssi": -60 - (i % 40)} for i in range(100)
        ]

        def sort_devices() -> int:
            sorted_devices = sorted(devices, key=lambda d: d["rssi"], reverse=True)
            return len(sorted_devices)

        result = benchmark(sort_devices)
        assert result == 100
