"""Unit tests for Telink BLE Mesh crypto operations."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.crypto import (
    _AES_BLOCK_SIZE,
    _SESSION_RANDOM_SIZE,
    crypt_payload,
    encrypt_mesh_credential,
    generate_session_random,
    make_checksum,
    make_pair_packet,
    make_session_key,
    telink_aes_encrypt,
    verify_checksum,
)
from tuya_ble_mesh.exceptions import AuthenticationError, CryptoError

# --- telink_aes_encrypt ---


class TestTelinkAesEncrypt:
    """Test the core AES-ECB with Telink byte reversal."""

    def test_output_is_16_bytes(self) -> None:
        key = b"\x00" * 16
        result = telink_aes_encrypt(key, b"\x00" * 16)
        assert len(result) == _AES_BLOCK_SIZE

    def test_different_keys_produce_different_output(self) -> None:
        pt = b"\x01" * 16
        r1 = telink_aes_encrypt(b"\x00" * 16, pt)
        r2 = telink_aes_encrypt(b"\x01" * 16, pt)
        assert r1 != r2

    def test_different_plaintexts_produce_different_output(self) -> None:
        key = b"\xaa" * 16
        r1 = telink_aes_encrypt(key, b"\x00" * 16)
        r2 = telink_aes_encrypt(key, b"\xff" * 16)
        assert r1 != r2

    def test_deterministic(self) -> None:
        key = b"\x42" * 16
        pt = b"\x13" * 16
        assert telink_aes_encrypt(key, pt) == telink_aes_encrypt(key, pt)

    def test_short_plaintext_is_padded(self) -> None:
        key = b"\x00" * 16
        result = telink_aes_encrypt(key, b"\x01\x02\x03")
        assert len(result) == _AES_BLOCK_SIZE

    def test_rejects_wrong_key_length(self) -> None:
        with pytest.raises(CryptoError, match="16 bytes"):
            telink_aes_encrypt(b"\x00" * 8, b"\x00" * 16)

    def test_byte_reversal_differs_from_standard_aes(self) -> None:
        """Telink AES reverses bytes — result differs from standard AES-ECB."""
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        key = b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\x0c\x0d\x0e\x0f\x10"
        pt = b"\x11\x12\x13\x14\x15\x16\x17\x18\x19\x1a\x1b\x1c\x1d\x1e\x1f\x20"

        # Standard AES-ECB (no byte reversal)
        cipher = Cipher(algorithms.AES(key), modes.ECB())  # nosec B305
        enc = cipher.encryptor()
        standard = enc.update(pt) + enc.finalize()

        # Telink AES-ECB (with byte reversal)
        telink = telink_aes_encrypt(key, pt)

        assert standard != telink


# --- generate_session_random ---


class TestGenerateSessionRandom:
    """Test random generation for handshake."""

    def test_length(self) -> None:
        r = generate_session_random()
        assert len(r) == _SESSION_RANDOM_SIZE

    def test_returns_bytes(self) -> None:
        assert isinstance(generate_session_random(), bytes)

    def test_two_calls_differ(self) -> None:
        r1 = generate_session_random()
        r2 = generate_session_random()
        assert r1 != r2


# --- make_pair_packet ---


class TestMakePairPacket:
    """Test pair request packet construction."""

    def test_length_is_17(self) -> None:
        packet = make_pair_packet(b"out_of_mesh", b"123456", b"\x01" * 8)
        assert len(packet) == 17

    def test_starts_with_0x0c(self) -> None:
        packet = make_pair_packet(b"out_of_mesh", b"123456", b"\x02" * 8)
        assert packet[0] == 0x0C

    def test_contains_session_random(self) -> None:
        rand = b"\xaa\xbb\xcc\xdd\xee\xff\x11\x22"
        packet = make_pair_packet(b"out_of_mesh", b"123456", rand)
        assert packet[1:9] == rand

    def test_encrypted_portion_is_8_bytes(self) -> None:
        packet = make_pair_packet(b"out_of_mesh", b"123456", b"\x03" * 8)
        assert len(packet[9:]) == 8

    def test_wrong_random_length_raises(self) -> None:
        with pytest.raises(CryptoError, match="8 bytes"):
            make_pair_packet(b"name", b"pass", b"\x01" * 4)

    def test_deterministic_with_same_inputs(self) -> None:
        args = (b"mesh", b"pass", b"\x05" * 8)
        assert make_pair_packet(*args) == make_pair_packet(*args)


# --- make_session_key ---


class TestMakeSessionKey:
    """Test session key derivation."""

    def test_output_is_16_bytes(self) -> None:
        key = make_session_key(b"name", b"pass", b"\x01" * 8, b"\x02" * 8)
        assert len(key) == _AES_BLOCK_SIZE

    def test_returns_bytes(self) -> None:
        key = make_session_key(b"name", b"pass", b"\x01" * 8, b"\x02" * 8)
        assert isinstance(key, bytes)

    def test_different_randoms_produce_different_keys(self) -> None:
        k1 = make_session_key(b"name", b"pass", b"\x01" * 8, b"\x02" * 8)
        k2 = make_session_key(b"name", b"pass", b"\x03" * 8, b"\x04" * 8)
        assert k1 != k2

    def test_different_credentials_produce_different_keys(self) -> None:
        rand_c = b"\x01" * 8
        rand_d = b"\x02" * 8
        k1 = make_session_key(b"out_of_mesh", b"123456", rand_c, rand_d)
        k2 = make_session_key(b"out_of_mesh", b"654321", rand_c, rand_d)
        assert k1 != k2

    def test_wrong_client_random_length(self) -> None:
        with pytest.raises(CryptoError, match="Client random"):
            make_session_key(b"name", b"pass", b"\x01" * 4, b"\x02" * 8)

    def test_wrong_device_random_length(self) -> None:
        with pytest.raises(CryptoError, match="Device random"):
            make_session_key(b"name", b"pass", b"\x01" * 8, b"\x02" * 12)

    def test_deterministic(self) -> None:
        args = (b"name", b"pass", b"\x11" * 8, b"\x22" * 8)
        assert make_session_key(*args) == make_session_key(*args)


# --- encrypt_mesh_credential ---


class TestEncryptMeshCredential:
    """Test credential encryption for set-mesh phase."""

    def test_output_is_16_bytes(self) -> None:
        key = b"\x00" * 16
        result = encrypt_mesh_credential(key, b"new_name")
        assert len(result) == _AES_BLOCK_SIZE

    def test_wrong_key_length(self) -> None:
        with pytest.raises(CryptoError, match="16 bytes"):
            encrypt_mesh_credential(b"\x00" * 10, b"data")


# --- crypt_payload (CTR mode) ---


class TestCryptPayload:
    """Test manual CTR mode encryption/decryption."""

    def test_roundtrip(self) -> None:
        key = b"\xab" * 16
        nonce = b"\x01\x02\x03\x04\x05\x06\x07\x08"
        plaintext = b"Hello Telink!!!"
        encrypted = crypt_payload(key, nonce, plaintext)
        decrypted = crypt_payload(key, nonce, encrypted)
        assert decrypted == plaintext

    def test_output_same_length_as_input(self) -> None:
        key = b"\x00" * 16
        nonce = b"\x00" * 8
        data = b"\x01\x02\x03\x04\x05"
        assert len(crypt_payload(key, nonce, data)) == len(data)

    def test_wrong_key_fails(self) -> None:
        key1 = b"\x01" * 16
        key2 = b"\x02" * 16
        nonce = b"\x00" * 8
        plaintext = b"secret data here"
        encrypted = crypt_payload(key1, nonce, plaintext)
        wrong_decrypt = crypt_payload(key2, nonce, encrypted)
        assert wrong_decrypt != plaintext

    def test_encrypts_to_different_value(self) -> None:
        key = b"\xff" * 16
        nonce = b"\x01" * 8
        plaintext = b"\x00" * 15
        encrypted = crypt_payload(key, nonce, plaintext)
        assert encrypted != plaintext

    def test_multi_block(self) -> None:
        key = b"\xcc" * 16
        nonce = b"\xdd" * 8
        plaintext = b"\x42" * 32  # 2 blocks
        encrypted = crypt_payload(key, nonce, plaintext)
        decrypted = crypt_payload(key, nonce, encrypted)
        assert decrypted == plaintext

    def test_wrong_key_length(self) -> None:
        with pytest.raises(CryptoError, match="16 bytes"):
            crypt_payload(b"\x00" * 8, b"\x00" * 8, b"data")

    def test_rejects_oversized_payload(self) -> None:
        """CTR counter is single byte — payloads over 4096 bytes must be rejected."""
        key = b"\x00" * 16
        nonce = b"\x00" * 8
        oversized = b"\x41" * 4097  # 257 blocks, exceeds 256-block limit
        with pytest.raises(CryptoError, match="too large"):
            crypt_payload(key, nonce, oversized)

    def test_accepts_max_size_payload(self) -> None:
        """Exactly 4096 bytes (256 blocks) should be accepted."""
        key = b"\xab" * 16
        nonce = b"\xcd" * 8
        max_payload = b"\x42" * 4096  # Exactly 256 blocks
        encrypted = crypt_payload(key, nonce, max_payload)
        decrypted = crypt_payload(key, nonce, encrypted)
        assert decrypted == max_payload


# --- make_checksum (CBC-MAC) ---


class TestMakeChecksum:
    """Test CBC-MAC computation."""

    def test_output_is_16_bytes(self) -> None:
        key = b"\x00" * 16
        nonce = b"\x00" * 8
        mac = make_checksum(key, nonce, b"payload")
        assert len(mac) == _AES_BLOCK_SIZE

    def test_deterministic(self) -> None:
        key = b"\x11" * 16
        nonce = b"\x22" * 8
        payload = b"\x33" * 15
        assert make_checksum(key, nonce, payload) == make_checksum(key, nonce, payload)

    def test_different_payloads_different_macs(self) -> None:
        key = b"\x44" * 16
        nonce = b"\x55" * 8
        m1 = make_checksum(key, nonce, b"\x00" * 15)
        m2 = make_checksum(key, nonce, b"\xff" * 15)
        assert m1 != m2

    def test_wrong_key_length(self) -> None:
        with pytest.raises(CryptoError, match="16 bytes"):
            make_checksum(b"\x00" * 4, b"\x00" * 8, b"data")


# --- verify_checksum ---


class TestVerifyChecksum:
    """Test CBC-MAC verification."""

    def test_valid_mac_passes(self) -> None:
        key = b"\xaa" * 16
        nonce = b"\xbb" * 8
        payload = b"\xcc" * 15
        mac = make_checksum(key, nonce, payload)
        assert verify_checksum(key, nonce, payload, mac[:2]) is True

    def test_invalid_mac_raises(self) -> None:
        key = b"\xaa" * 16
        nonce = b"\xbb" * 8
        payload = b"\xcc" * 15
        with pytest.raises(AuthenticationError, match="MAC verification"):
            verify_checksum(key, nonce, payload, b"\x00\x00")

    def test_tampered_payload_fails(self) -> None:
        key = b"\xdd" * 16
        nonce = b"\xee" * 8
        payload = b"\xff" * 15
        mac = make_checksum(key, nonce, payload)
        tampered = b"\x00" * 15
        with pytest.raises(AuthenticationError):
            verify_checksum(key, nonce, tampered, mac[:2])
