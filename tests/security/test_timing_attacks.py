"""Timing attack and side-channel tests.

Tests for timing-based side-channel vulnerabilities:
- Constant-time crypto operations
- No timing leaks in checksum verification
- Resistance to timing-based attacks
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "custom_components"
        / "tuya_ble_mesh"
        / "lib"
    ),
)

import contextlib

from tuya_ble_mesh.crypto import make_checksum, verify_checksum
from tuya_ble_mesh.exceptions import AuthenticationError

# Number of iterations for timing measurements
_TIMING_ITERATIONS = 500


class TestConstantTimeChecksum:
    """Verify checksum operations don't leak timing information."""

    def test_verify_checksum_timing_valid(self) -> None:
        """Valid checksums should take consistent time."""
        key = b"test_key_16bytes"
        nonce = b"test_nonce_here\x00"
        data = b"test_data_here"

        times = []
        for i in range(_TIMING_ITERATIONS):
            # Use varying data to prevent caching effects
            test_data = data + bytes([i % 256])
            checksum = make_checksum(key, nonce, test_data)

            start = time.perf_counter()
            verify_checksum(key, nonce, test_data, checksum)
            elapsed = time.perf_counter() - start
            times.append(elapsed)

        # Calculate statistics
        mean_time = statistics.mean(times)
        stddev = statistics.stdev(times)

        # Standard deviation should be small relative to mean
        assert stddev < mean_time * 0.5, (
            f"Checksum verification shows high timing variance: "
            f"mean={mean_time * 1e6:.2f}µs, stddev={stddev * 1e6:.2f}µs"
        )

    def test_verify_checksum_timing_invalid(self) -> None:
        """Invalid checksums should take consistent time (constant-time compare)."""
        key = b"test_key_16bytes"
        nonce = b"test_nonce_here\x00"
        data = b"test_data_here"
        valid_checksum = make_checksum(key, nonce, data)

        times = []
        for i in range(_TIMING_ITERATIONS):
            # Create invalid checksums with varying bit differences
            invalid_checksum = bytes([(valid_checksum[j] ^ (i % 256)) for j in range(2)])

            start = time.perf_counter()
            with contextlib.suppress(AuthenticationError):
                verify_checksum(key, nonce, data, invalid_checksum)
            elapsed = time.perf_counter() - start
            times.append(elapsed)

        stddev = statistics.stdev(times)
        mean_time = statistics.mean(times)

        # Invalid checksums should take similar time regardless of differences
        assert stddev < mean_time * 0.5, (
            f"Invalid checksum timing varies too much: "
            f"stddev={stddev * 1e6:.2f}µs (mean={mean_time * 1e6:.2f}µs)"
        )

    def test_valid_vs_invalid_timing_similar(self) -> None:
        """Valid and invalid checksum verification should take similar time."""
        key = b"test_key_16bytes"
        nonce = b"test_nonce_here\x00"
        data = b"test_data_here"
        valid_checksum = make_checksum(key, nonce, data)
        invalid_checksum = b"\xff\xff"

        valid_times = []
        invalid_times = []

        for _ in range(_TIMING_ITERATIONS):
            # Time valid checksum
            start = time.perf_counter()
            verify_checksum(key, nonce, data, valid_checksum)
            valid_times.append(time.perf_counter() - start)

            # Time invalid checksum
            start = time.perf_counter()
            with contextlib.suppress(AuthenticationError):
                verify_checksum(key, nonce, data, invalid_checksum)
            invalid_times.append(time.perf_counter() - start)

        valid_mean = statistics.mean(valid_times)
        invalid_mean = statistics.mean(invalid_times)

        # Means should be within reasonable range to prevent timing oracle
        ratio = max(valid_mean, invalid_mean) / min(valid_mean, invalid_mean)
        assert ratio < 2.0, (
            f"Valid vs invalid checksum timing differs too much: "
            f"ratio={ratio:.2f} (valid={valid_mean * 1e6:.2f}µs, "
            f"invalid={invalid_mean * 1e6:.2f}µs)"
        )


class TestNoTimingLeaksInComparison:
    """Test that byte comparisons don't leak information via timing."""

    def test_checksum_compare_position_independent(self) -> None:
        """Checksum differing in different positions should take same time."""
        key = b"test_key_16bytes"
        nonce = b"test_nonce_here\x00"
        data = b"test_data_here"
        valid = make_checksum(key, nonce, data)

        # Create checksum differing in first byte
        first_diff = bytes([valid[0] ^ 0xFF, valid[1]])
        # Create checksum differing in second byte
        second_diff = bytes([valid[0], valid[1] ^ 0xFF])

        first_times = []
        second_times = []

        for _ in range(_TIMING_ITERATIONS):
            start = time.perf_counter()
            with contextlib.suppress(AuthenticationError):
                verify_checksum(key, nonce, data, first_diff)
            first_times.append(time.perf_counter() - start)

            start = time.perf_counter()
            with contextlib.suppress(AuthenticationError):
                verify_checksum(key, nonce, data, second_diff)
            second_times.append(time.perf_counter() - start)

        first_mean = statistics.mean(first_times)
        second_mean = statistics.mean(second_times)

        # Both should take similar time (constant-time compare)
        ratio = max(first_mean, second_mean) / min(first_mean, second_mean)
        assert ratio < 1.8, (
            f"Checksum position affects timing: first={first_mean * 1e6:.2f}µs, "
            f"second={second_mean * 1e6:.2f}µs, ratio={ratio:.2f}"
        )


class TestCacheTimingChannels:
    """Test resistance to cache-timing attacks."""

    def test_different_keys_similar_timing(self) -> None:
        """Checksum with different keys should take similar time."""
        nonce = b"test_nonce_here\x00"
        data = b"test_data_here"

        # Keys with different byte patterns
        key_zeros = b"\x00" * 16
        key_ones = b"\xff" * 16
        key_alternating = b"\xaa\x55" * 8

        times_zeros = []
        times_ones = []
        times_alt = []

        for _ in range(_TIMING_ITERATIONS):
            cs_zeros = make_checksum(key_zeros, nonce, data)
            start = time.perf_counter()
            verify_checksum(key_zeros, nonce, data, cs_zeros)
            times_zeros.append(time.perf_counter() - start)

            cs_ones = make_checksum(key_ones, nonce, data)
            start = time.perf_counter()
            verify_checksum(key_ones, nonce, data, cs_ones)
            times_ones.append(time.perf_counter() - start)

            cs_alt = make_checksum(key_alternating, nonce, data)
            start = time.perf_counter()
            verify_checksum(key_alternating, nonce, data, cs_alt)
            times_alt.append(time.perf_counter() - start)

        mean_zeros = statistics.mean(times_zeros)
        mean_ones = statistics.mean(times_ones)
        mean_alt = statistics.mean(times_alt)

        # All key patterns should take similar time
        max_mean = max(mean_zeros, mean_ones, mean_alt)
        min_mean = min(mean_zeros, mean_ones, mean_alt)
        ratio = max_mean / min_mean

        assert ratio < 1.8, (
            f"Key pattern affects timing: zeros={mean_zeros * 1e6:.2f}µs, "
            f"ones={mean_ones * 1e6:.2f}µs, alt={mean_alt * 1e6:.2f}µs"
        )
