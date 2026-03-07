"""Simple performance tests without pytest-benchmark.

Measures basic performance metrics for crypto, protocol, and scanner operations.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))


class TestCryptoPerformance:
    """Test crypto operations performance."""

    def test_crypto_checksum_throughput(self) -> None:
        """Measure checksum generation throughput."""
        from tuya_ble_mesh.crypto import make_checksum

        key = b"0123456789ABCDEF"
        nonce = b"nonce_test_data\x00"
        payload = b"test_payload_15"

        iterations = 1000
        start = time.perf_counter()

        for _ in range(iterations):
            make_checksum(key, nonce, payload)

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        # Should process at least 10k ops/sec (very conservative)
        assert ops_per_sec > 10_000, f"Too slow: {ops_per_sec:.0f} ops/sec"

    def test_crypto_encryption_throughput(self) -> None:
        """Measure encryption throughput."""
        from tuya_ble_mesh.crypto import crypt_payload

        key = b"0123456789ABCDEF"
        nonce = b"nonce_test_data\x00"
        payload = b"test_payload_15"

        iterations = 1000
        start = time.perf_counter()

        for _ in range(iterations):
            crypt_payload(key, nonce, payload)

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        # Should process at least 10k ops/sec
        assert ops_per_sec > 10_000, f"Too slow: {ops_per_sec:.0f} ops/sec"

    def test_crypto_verify_checksum_throughput(self) -> None:
        """Measure checksum verification throughput."""
        from tuya_ble_mesh.crypto import make_checksum, verify_checksum

        key = b"0123456789ABCDEF"
        nonce = b"nonce_test_data\x00"
        payload = b"test_payload_15"
        checksum = make_checksum(key, nonce, payload)

        iterations = 1000
        start = time.perf_counter()

        for _ in range(iterations):
            verify_checksum(key, nonce, payload, checksum)

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        # Should process at least 10k ops/sec
        assert ops_per_sec > 10_000, f"Too slow: {ops_per_sec:.0f} ops/sec"


class TestProtocolPerformance:
    """Test protocol operations performance."""

    def test_protocol_encode_throughput(self) -> None:
        """Measure protocol encoding throughput."""
        from tuya_ble_mesh.protocol import build_command_packet

        key = b"0123456789ABCDEF"
        mac = b"\xdc\x23\x4d\x21\x43\xa5"
        params = b"\x01\x00"

        iterations = 1000
        start = time.perf_counter()

        for seq in range(iterations):
            build_command_packet(key, mac, seq, 0x01, 0xC1, params)

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        # Should process at least 5k ops/sec
        assert ops_per_sec > 5_000, f"Too slow: {ops_per_sec:.0f} ops/sec"

    def test_protocol_decode_throughput(self) -> None:
        """Measure protocol decoding throughput."""
        from tuya_ble_mesh.protocol import build_command_packet, decode_command_packet

        key = b"0123456789ABCDEF"
        mac = b"\xdc\x23\x4d\x21\x43\xa5"
        params = b"\x01\x00"

        # Pre-generate packets
        packets = [
            build_command_packet(key, mac, seq, 0x01, 0xC1, params)
            for seq in range(100)
        ]

        iterations = 1000
        start = time.perf_counter()

        for i in range(iterations):
            decode_command_packet(key, mac, packets[i % 100])

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        # Should process at least 5k ops/sec
        assert ops_per_sec > 5_000, f"Too slow: {ops_per_sec:.0f} ops/sec"

    def test_protocol_roundtrip_throughput(self) -> None:
        """Measure full encode+decode cycle throughput."""
        from tuya_ble_mesh.protocol import build_command_packet, decode_command_packet

        key = b"0123456789ABCDEF"
        mac = b"\xdc\x23\x4d\x21\x43\xa5"
        params = b"\x01\x00"

        iterations = 500
        start = time.perf_counter()

        for seq in range(iterations):
            packet = build_command_packet(key, mac, seq, 0x01, 0xC1, params)
            decoded = decode_command_packet(key, mac, packet)
            assert decoded.sequence == seq

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        # Should process at least 2k roundtrips/sec
        assert ops_per_sec > 2_000, f"Too slow: {ops_per_sec:.0f} ops/sec"


class TestDeviceCreationPerformance:
    """Test device object creation performance."""

    def test_mesh_device_creation_throughput(self) -> None:
        """Measure MeshDevice instantiation throughput."""
        from tuya_ble_mesh.device import MeshDevice

        mock_ble = MagicMock()

        iterations = 1000
        start = time.perf_counter()

        for i in range(iterations):
            addr = f"DC:23:4D:21:43:{i % 256:02X}"
            MeshDevice(addr, i % 256, 0x0001, mock_ble)

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        # Should create at least 10k devices/sec
        assert ops_per_sec > 10_000, f"Too slow: {ops_per_sec:.0f} devices/sec"

    def test_sig_mesh_device_creation_throughput(self) -> None:
        """Measure SIGMeshDevice instantiation throughput."""
        from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice

        mock_ble = MagicMock()

        iterations = 1000
        start = time.perf_counter()

        for i in range(iterations):
            addr = f"DC:23:4D:21:43:{i % 256:02X}"
            SIGMeshDevice(addr, 0x00AA, 0x0001, mock_ble)

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        # Should create at least 10k devices/sec
        assert ops_per_sec > 10_000, f"Too slow: {ops_per_sec:.0f} devices/sec"


class TestCoordinatorPerformance:
    """Test coordinator performance."""

    def test_coordinator_creation_throughput(self) -> None:
        """Measure coordinator instantiation throughput."""
        from custom_components.tuya_ble_mesh.coordinator import (
            TuyaBLEMeshCoordinator,
        )

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        iterations = 1000
        start = time.perf_counter()

        for _ in range(iterations):
            TuyaBLEMeshCoordinator(mock_device)

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        # Should create at least 5k coordinators/sec
        assert ops_per_sec > 5_000, f"Too slow: {ops_per_sec:.0f} coordinators/sec"

    def test_coordinator_listener_notification_throughput(self) -> None:
        """Measure listener notification throughput."""
        from custom_components.tuya_ble_mesh.coordinator import (
            TuyaBLEMeshCoordinator,
        )

        mock_device = MagicMock()
        mock_device.address = "DC:23:4D:21:43:A5"

        coord = TuyaBLEMeshCoordinator(mock_device)

        # Add 10 listeners
        for _ in range(10):
            coord.add_listener(lambda: None)

        iterations = 1000
        start = time.perf_counter()

        for _ in range(iterations):
            coord._notify_listeners()

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        # Should notify at least 10k times/sec
        assert ops_per_sec > 10_000, f"Too slow: {ops_per_sec:.0f} notifications/sec"


class TestSequenceNumberPerformance:
    """Test sequence number operations performance."""

    @pytest.mark.asyncio
    async def test_sequence_generation_throughput(self) -> None:
        """Measure sequence number generation throughput."""
        from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice

        mock_ble = MagicMock()
        device = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, mock_ble)
        device.set_seq(1000)

        iterations = 500
        start = time.perf_counter()

        for _ in range(iterations):
            await device._next_seq()

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        # Should generate at least 5k seqs/sec
        assert ops_per_sec > 5_000, f"Too slow: {ops_per_sec:.0f} seqs/sec"


class TestBulkOperationPerformance:
    """Test bulk operations performance."""

    def test_filter_advertisements_throughput(self) -> None:
        """Measure advertisement filtering throughput."""
        # Simulate 1000 BLE advertisements
        advertisements = [
            {
                "address": f"AA:BB:CC:DD:EE:{i % 256:02X}",
                "name": f"Tuya_{i}" if i % 3 == 0 else f"Device_{i}",
                "rssi": -60 - (i % 40),
            }
            for i in range(1000)
        ]

        iterations = 100
        start = time.perf_counter()

        for _ in range(iterations):
            filtered = [
                ad for ad in advertisements if ad["name"].startswith("Tuya_")
            ]
            assert len(filtered) > 0

        elapsed = time.perf_counter() - start
        ops_per_sec = iterations / elapsed

        # Should filter at least 500 times/sec
        assert ops_per_sec > 500, f"Too slow: {ops_per_sec:.0f} filters/sec"
