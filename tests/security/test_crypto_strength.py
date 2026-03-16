"""Security tests for Telink mesh crypto.

Verifies key length, randomness, and that crypto operations
do not leak key material.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.crypto import (
    _AES_BLOCK_SIZE,
    _SESSION_RANDOM_SIZE,
    generate_session_random,
    make_pair_packet,
    make_session_key,
    telink_aes_encrypt,
)
from tuya_ble_mesh.exceptions import CryptoError


class TestKeyLength:
    """Verify all keys are exactly 128-bit (16 bytes)."""

    def test_session_key_is_128_bit(self) -> None:
        key = make_session_key(b"name", b"pass", b"\x01" * 8, b"\x02" * 8)
        assert len(key) == _AES_BLOCK_SIZE

    def test_aes_output_is_128_bit(self) -> None:
        result = telink_aes_encrypt(b"\x00" * 16, b"\x00" * 16)
        assert len(result) == _AES_BLOCK_SIZE

    def test_rejects_short_key(self) -> None:
        with pytest.raises(CryptoError):
            telink_aes_encrypt(b"\x00" * 8, b"\x00" * 16)

    def test_rejects_long_key(self) -> None:
        with pytest.raises(CryptoError):
            telink_aes_encrypt(b"\x00" * 32, b"\x00" * 16)


class TestRandomness:
    """Verify that random generation has sufficient entropy."""

    def test_random_length(self) -> None:
        assert len(generate_session_random()) == _SESSION_RANDOM_SIZE

    def test_100_randoms_are_unique(self) -> None:
        samples = {generate_session_random() for _ in range(100)}
        assert len(samples) == 100

    def test_random_bytes_not_all_zero(self) -> None:
        samples = [generate_session_random() for _ in range(10)]
        all_zero = [s == b"\x00" * _SESSION_RANDOM_SIZE for s in samples]
        assert not any(all_zero)


class TestPairPacketIntegrity:
    """Verify pair packet structure and bounds."""

    def test_pair_packet_fixed_length(self) -> None:
        packet = make_pair_packet(b"mesh", b"pass", b"\xaa" * 8)
        assert len(packet) == 17

    def test_pair_packet_opcode(self) -> None:
        packet = make_pair_packet(b"mesh", b"pass", b"\xbb" * 8)
        assert packet[0] == 0x0C

    def test_different_credentials_different_proof(self) -> None:
        rand = b"\xcc" * 8
        # Use credentials where name XOR password actually differs
        p1 = make_pair_packet(b"out_of_mesh", b"123456", rand)
        p2 = make_pair_packet(b"my_network", b"abcdef", rand)
        # Opcode and random are same, encrypted proof differs
        assert p1[:9] == p2[:9]
        assert p1[9:] != p2[9:]


class TestNoKeyLeakage:
    """Verify that exception messages do not contain key material."""

    def test_short_key_error_no_key_value(self) -> None:
        try:
            telink_aes_encrypt(b"\xde\xad\xbe\xef", b"\x00" * 16)
        except CryptoError as exc:
            msg = str(exc)
            assert "dead" not in msg.lower()
            assert "\\xde" not in msg
            assert "16 bytes" in msg

    def test_session_random_error_no_value(self) -> None:
        try:
            make_pair_packet(b"name", b"pass", b"\xab\xcd")
        except CryptoError as exc:
            msg = str(exc)
            assert "\\xab" not in msg
            assert "8 bytes" in msg
