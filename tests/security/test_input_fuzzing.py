"""Input fuzzing tests for protocol module.

Feeds 10,000 random byte sequences to protocol parsers to verify
they never crash - only raise proper exceptions.
"""

import contextlib
import os
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from tuya_ble_mesh.exceptions import MalmbergsBTError
from tuya_ble_mesh.protocol import (
    decode_command_packet,
    decode_dp_value,
    decode_dps_response,
    decode_status,
    decrypt_notification,
    parse_pair_response,
)

# All protocol exceptions inherit from MalmbergsBTError
_ALLOWED_EXCEPTIONS = (MalmbergsBTError,)

# Fixed test key and MAC for decode functions that need crypto
_FUZZ_KEY = b"\x00" * 16
_FUZZ_MAC = b"\x00" * 6

# Number of random inputs per test
_FUZZ_COUNT = 10_000


class TestFuzzDecodeCommandPacket:
    """Fuzz decode_command_packet with random inputs."""

    def test_random_20_byte_packets(self) -> None:
        """10,000 random 20-byte packets must not crash."""
        for _ in range(_FUZZ_COUNT):
            data = os.urandom(20)
            with contextlib.suppress(*_ALLOWED_EXCEPTIONS):
                decode_command_packet(_FUZZ_KEY, _FUZZ_MAC, data)

    def test_random_lengths(self) -> None:
        """Random-length inputs must raise MalformedPacketError, not crash."""
        for _ in range(_FUZZ_COUNT):
            length = int.from_bytes(os.urandom(1), "big") % 64
            data = os.urandom(length)
            with contextlib.suppress(*_ALLOWED_EXCEPTIONS):
                decode_command_packet(_FUZZ_KEY, _FUZZ_MAC, data)


class TestFuzzDecryptNotification:
    """Fuzz decrypt_notification with random inputs."""

    def test_random_notifications(self) -> None:
        for _ in range(_FUZZ_COUNT):
            length = 6 + int.from_bytes(os.urandom(1), "big") % 30
            data = os.urandom(length)
            with contextlib.suppress(*_ALLOWED_EXCEPTIONS):
                decrypt_notification(_FUZZ_KEY, _FUZZ_MAC, data)

    def test_short_notifications(self) -> None:
        for _ in range(_FUZZ_COUNT):
            length = int.from_bytes(os.urandom(1), "big") % 6
            data = os.urandom(max(length, 1))
            with contextlib.suppress(*_ALLOWED_EXCEPTIONS):
                decrypt_notification(_FUZZ_KEY, _FUZZ_MAC, data)


class TestFuzzDecodeStatus:
    """Fuzz decode_status with random inputs."""

    def test_random_status_data(self) -> None:
        for _ in range(_FUZZ_COUNT):
            length = int.from_bytes(os.urandom(1), "big") % 32
            data = os.urandom(max(length, 1))
            with contextlib.suppress(*_ALLOWED_EXCEPTIONS):
                decode_status(data)

    def test_valid_length_random_content(self) -> None:
        """20-byte random data should always parse successfully."""
        for _ in range(_FUZZ_COUNT):
            data = os.urandom(20)
            # 20 bytes is always enough - should never raise
            status = decode_status(data)
            assert 0 <= status.mesh_id <= 255
            assert 0 <= status.red <= 255


class TestFuzzParsePairResponse:
    """Fuzz parse_pair_response with random inputs."""

    def test_random_pair_responses(self) -> None:
        for _ in range(_FUZZ_COUNT):
            length = 1 + int.from_bytes(os.urandom(1), "big") % 20
            data = os.urandom(length)
            with contextlib.suppress(*_ALLOWED_EXCEPTIONS):
                parse_pair_response(data)

    def test_empty_input(self) -> None:
        with pytest.raises(_ALLOWED_EXCEPTIONS):
            parse_pair_response(b"")


class TestFuzzDecodeDpValue:
    """Fuzz decode_dp_value with random inputs."""

    def test_random_dp_tlvs(self) -> None:
        for _ in range(_FUZZ_COUNT):
            length = int.from_bytes(os.urandom(1), "big") % 32
            data = os.urandom(max(length, 1))
            with contextlib.suppress(*_ALLOWED_EXCEPTIONS):
                decode_dp_value(data)

    def test_valid_header_random_payload(self) -> None:
        """Valid TLV header with random payload content."""
        for _ in range(_FUZZ_COUNT):
            dp_id = (int.from_bytes(os.urandom(1), "big") % 255) + 1
            dp_type = int.from_bytes(os.urandom(1), "big") % 6
            val_len = int.from_bytes(os.urandom(1), "big") % 16
            header = struct.pack(">BBH", dp_id, dp_type, val_len)
            payload = os.urandom(val_len)
            with contextlib.suppress(*_ALLOWED_EXCEPTIONS):
                decode_dp_value(header + payload)


class TestFuzzDecodeDpsResponse:
    """Fuzz decode_dps_response with random inputs."""

    def test_random_dp_streams(self) -> None:
        for _ in range(_FUZZ_COUNT):
            length = int.from_bytes(os.urandom(1), "big") % 64
            data = os.urandom(max(length, 1))
            with contextlib.suppress(*_ALLOWED_EXCEPTIONS):
                decode_dps_response(data)
