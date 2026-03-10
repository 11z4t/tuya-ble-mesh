"""Protocol performance benchmarks.

Measures protocol encoding/decoding performance.
"""

from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from tuya_ble_mesh.protocol import (
    decode_command_packet,
    decode_dp_value,
    decode_status,
    encode_command_packet,
    encode_command_payload,
)

# Test data
_KEY = b"0123456789ABCDEF"
_MAC = b"\xdc\x23\x4d\x21\x43\xa5"
_SEQUENCE = 1000
_DEST_ID = 0x01
_OPCODE = 0xC1


class TestProtocolEncodingPerformance:
    """Benchmark protocol encoding operations."""

    def test_benchmark_encode_command_payload(self, benchmark) -> None:
        """Benchmark command payload encoding."""
        params = b"\x01\x00"

        result = benchmark(encode_command_payload, _DEST_ID, _OPCODE, params)
        assert len(result) > 0

    def test_benchmark_encode_command_packet(self, benchmark) -> None:
        """Benchmark full command packet construction."""
        params = b"\x01\x00"

        result = benchmark(encode_command_packet, _KEY, _MAC, _SEQUENCE, _DEST_ID, _OPCODE, params)
        assert len(result) == 20


class TestProtocolDecodingPerformance:
    """Benchmark protocol decoding operations."""

    def test_benchmark_decode_command_packet(self, benchmark) -> None:
        """Benchmark command packet decoding."""
        packet = encode_command_packet(_KEY, _MAC, _SEQUENCE, _DEST_ID, _OPCODE, b"\x01\x00")

        result = benchmark(decode_command_packet, _KEY, _MAC, packet)
        assert result.sequence == _SEQUENCE

    def test_benchmark_decode_status(self, benchmark) -> None:
        """Benchmark status response parsing."""
        # Valid 20-byte status buffer with correct offsets per STATUS_OFFSET_* constants
        status_data = bytearray(20)
        status_data[3] = 0x01   # mesh_id at offset 3
        status_data[12] = 0x02  # mode at offset 12
        status_data[13] = 0x64  # white_brightness at offset 13
        status_data[14] = 0x32  # white_temp at offset 14
        status_data[15] = 0x80  # color_brightness at offset 15
        status_data[16] = 0xFF  # red at offset 16
        status_data[17] = 0x00  # green at offset 17
        status_data[18] = 0x00  # blue at offset 18

        result = benchmark(decode_status, bytes(status_data))
        assert result.mesh_id == 0x01

    def test_benchmark_decode_dp_value(self, benchmark) -> None:
        """Benchmark DP TLV decoding."""
        dp_id = 1
        dp_type = 2  # DP_TYPE_VALUE
        value = 100
        value_bytes = value.to_bytes(4, "big")
        data = struct.pack(">BBH", dp_id, dp_type, 4) + value_bytes

        result = benchmark(decode_dp_value, data)
        assert result[0] == dp_id


class TestProtocolRoundtripPerformance:
    """Benchmark full encode/decode cycles."""

    def test_benchmark_command_roundtrip(self, benchmark) -> None:
        """Benchmark full command encode + decode cycle."""

        def roundtrip() -> int:
            packet = encode_command_packet(_KEY, _MAC, _SEQUENCE, _DEST_ID, _OPCODE, b"\x01\x00")
            decoded = decode_command_packet(_KEY, _MAC, packet)
            return decoded.sequence

        result = benchmark(roundtrip)
        assert result == _SEQUENCE

    def test_benchmark_1000_command_roundtrips(self, benchmark) -> None:
        """Benchmark 1000 full command roundtrips."""

        def roundtrip_bulk() -> int:
            count = 0
            for seq in range(1000):
                packet = encode_command_packet(_KEY, _MAC, seq, _DEST_ID, _OPCODE, b"\x01\x00")
                decoded = decode_command_packet(_KEY, _MAC, packet)
                assert decoded.sequence == seq
                count += 1
            return count

        result = benchmark(roundtrip_bulk)
        assert result == 1000


class TestProtocolPayloadSizes:
    """Benchmark protocol with varying payload sizes."""

    @pytest.mark.parametrize("param_len", [0, 2, 5, 10])
    def test_benchmark_varying_param_sizes(self, benchmark, param_len: int) -> None:
        """Benchmark encoding with varying parameter sizes."""
        params = os.urandom(param_len)

        result = benchmark(encode_command_packet, _KEY, _MAC, _SEQUENCE, _DEST_ID, _OPCODE, params)
        assert len(result) == 20


@pytest.mark.skipif(
    "BENCHMARK_SLOW" not in os.environ,
    reason="Slow benchmarks skipped (set BENCHMARK_SLOW=1 to run)",
)
class TestProtocolStressBenchmarks:
    """Stress test benchmarks (slow, opt-in)."""

    def test_benchmark_100k_encode_operations(self, benchmark) -> None:
        """Benchmark encoding 100,000 packets."""

        def encode_stress() -> int:
            count = 0
            for seq in range(100_000):
                encode_command_packet(_KEY, _MAC, seq % 0xFFFFFF, _DEST_ID, _OPCODE, b"\x01\x00")
                count += 1
            return count

        result = benchmark(encode_stress)
        assert result == 100_000

    def test_benchmark_100k_decode_operations(self, benchmark) -> None:
        """Benchmark decoding 100,000 packets."""
        # Pre-generate packets
        packets = [
            encode_command_packet(_KEY, _MAC, seq % 0xFFFFFF, _DEST_ID, _OPCODE, b"\x01\x00")
            for seq in range(1000)
        ]

        def decode_stress() -> int:
            count = 0
            for _ in range(100):
                for packet in packets:
                    decode_command_packet(_KEY, _MAC, packet)
                    count += 1
            return count

        result = benchmark(decode_stress)
        assert result == 100_000
