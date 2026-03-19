"""Crypto performance benchmarks.

Measures crypto operations performance to detect regressions.
Benchmarks use pytest-benchmark for consistent measurement.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "custom_components"
        / "tuya_ble_mesh"
        / "lib"
    ),
)

pytest.importorskip("pytest_benchmark")

import contextlib

from tuya_ble_mesh.crypto import crypt_payload, make_checksum, verify_checksum

# Test data
_KEY = b"0123456789ABCDEF"  # 16 bytes
_NONCE = b"nonce_test_data\x00"  # 16 bytes
_PAYLOAD = b"test_payload_15"  # 15 bytes (standard payload size)
_CHECKSUM = make_checksum(_KEY, _NONCE, _PAYLOAD)


class TestCryptoPerformance:
    """Benchmark crypto operations."""

    def test_benchmark_make_checksum(self, benchmark) -> None:
        """Benchmark checksum generation."""
        result = benchmark(make_checksum, _KEY, _NONCE, _PAYLOAD)
        assert len(result) == 16

    def test_benchmark_verify_checksum_valid(self, benchmark) -> None:
        """Benchmark valid checksum verification."""
        benchmark(verify_checksum, _KEY, _NONCE, _PAYLOAD, _CHECKSUM)

    def test_benchmark_verify_checksum_invalid(self, benchmark) -> None:
        """Benchmark invalid checksum verification (should raise)."""
        invalid = b"\xff\xff"

        def verify_invalid() -> None:
            with contextlib.suppress(Exception):
                verify_checksum(_KEY, _NONCE, _PAYLOAD, invalid)

        benchmark(verify_invalid)

    def test_benchmark_crypt_payload_encrypt(self, benchmark) -> None:
        """Benchmark payload encryption."""
        result = benchmark(crypt_payload, _KEY, _NONCE, _PAYLOAD)
        assert len(result) == len(_PAYLOAD)

    def test_benchmark_crypt_payload_decrypt(self, benchmark) -> None:
        """Benchmark payload decryption (same operation as encrypt in CTR)."""
        encrypted = crypt_payload(_KEY, _NONCE, _PAYLOAD)
        result = benchmark(crypt_payload, _KEY, _NONCE, encrypted)
        assert result == _PAYLOAD


class TestCryptoBulkPerformance:
    """Benchmark bulk crypto operations."""

    def test_benchmark_encrypt_1000_packets(self, benchmark) -> None:
        """Benchmark encrypting 1000 packets."""

        def encrypt_bulk() -> int:
            count = 0
            for i in range(1000):
                nonce = _NONCE[:15] + bytes([i % 256])
                crypt_payload(_KEY, nonce, _PAYLOAD)
                count += 1
            return count

        result = benchmark(encrypt_bulk)
        assert result == 1000

    def test_benchmark_checksum_1000_packets(self, benchmark) -> None:
        """Benchmark computing checksums for 1000 packets."""

        def checksum_bulk() -> int:
            count = 0
            for i in range(1000):
                nonce = _NONCE[:15] + bytes([i % 256])
                make_checksum(_KEY, nonce, _PAYLOAD)
                count += 1
            return count

        result = benchmark(checksum_bulk)
        assert result == 1000


class TestCryptoScalability:
    """Benchmark crypto scalability with varying data sizes."""

    @pytest.mark.parametrize("size", [15, 50, 100, 200, 500])
    def test_benchmark_varying_payload_sizes(self, benchmark, size: int) -> None:
        """Benchmark encryption with varying payload sizes."""
        payload = os.urandom(size)

        result = benchmark(crypt_payload, _KEY, _NONCE, payload)
        assert len(result) == size


@pytest.mark.skipif(
    "BENCHMARK_SLOW" not in os.environ,
    reason="Slow benchmarks skipped (set BENCHMARK_SLOW=1 to run)",
)
class TestCryptoStressBenchmarks:
    """Stress test benchmarks (slow, opt-in via env var)."""

    def test_benchmark_encrypt_100k_packets(self, benchmark) -> None:
        """Benchmark encrypting 100,000 packets."""

        def encrypt_stress() -> int:
            count = 0
            for i in range(100_000):
                nonce = _NONCE[:15] + bytes([i % 256])
                crypt_payload(_KEY, nonce, _PAYLOAD)
                count += 1
            return count

        result = benchmark(encrypt_stress)
        assert result == 100_000
