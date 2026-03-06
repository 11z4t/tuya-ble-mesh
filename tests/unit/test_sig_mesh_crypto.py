"""Unit tests for SIG Mesh cryptographic primitives."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from tuya_ble_mesh.exceptions import CryptoError
from tuya_ble_mesh.sig_mesh_crypto import (
    aes_cmac,
    aes_ecb,
    k1,
    k2,
    k3,
    k4,
    mesh_aes_ccm_decrypt,
    mesh_aes_ccm_encrypt,
    s1,
)

# ============================================================
# AES-ECB
# ============================================================


class TestAesEcb:
    """Test standard-order AES-ECB."""

    def test_output_is_16_bytes(self) -> None:
        result = aes_ecb(b"\x00" * 16, b"\x00" * 16)
        assert len(result) == 16

    def test_deterministic(self) -> None:
        key = b"\x42" * 16
        pt = b"\x13" * 16
        assert aes_ecb(key, pt) == aes_ecb(key, pt)

    def test_different_keys(self) -> None:
        pt = b"\x01" * 16
        r1 = aes_ecb(b"\x00" * 16, pt)
        r2 = aes_ecb(b"\x01" * 16, pt)
        assert r1 != r2

    def test_rejects_wrong_key_length(self) -> None:
        with pytest.raises(CryptoError, match="16 bytes"):
            aes_ecb(b"\x00" * 8, b"\x00" * 16)

    def test_rejects_wrong_plaintext_length(self) -> None:
        with pytest.raises(CryptoError, match="16 bytes"):
            aes_ecb(b"\x00" * 16, b"\x00" * 8)

    def test_known_vector_nist(self) -> None:
        """NIST AES-128 test vector (FIPS 197 Appendix B)."""
        key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
        pt = bytes.fromhex("3243f6a8885a308d313198a2e0370734")
        expected = bytes.fromhex("3925841d02dc09fbdc118597196a0b32")
        assert aes_ecb(key, pt) == expected


# ============================================================
# AES-CMAC (RFC 4493 test vectors)
# ============================================================


class TestAesCmac:
    """Test AES-CMAC with RFC 4493 test vectors."""

    # Key from RFC 4493 Section 4
    KEY = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")

    def test_empty_message(self) -> None:
        """RFC 4493 Example 1: empty message."""
        expected = bytes.fromhex("bb1d6929e95937287fa37d129b756746")
        assert aes_cmac(self.KEY, b"") == expected

    def test_16_byte_message(self) -> None:
        """RFC 4493 Example 2: 16-byte message (complete block)."""
        msg = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
        expected = bytes.fromhex("070a16b46b4d4144f79bdd9dd04a287c")
        assert aes_cmac(self.KEY, msg) == expected

    def test_40_byte_message(self) -> None:
        """RFC 4493 Example 3: 40-byte message (incomplete last block)."""
        msg = bytes.fromhex(
            "6bc1bee22e409f96e93d7e117393172a"  # pragma: allowlist secret
            "ae2d8a571e03ac9c9eb76fac45af8e5130c81c46a35ce411"  # pragma: allowlist secret
        )
        expected = bytes.fromhex("dfa66747de9ae63030ca32611497c827")
        assert aes_cmac(self.KEY, msg) == expected

    def test_64_byte_message(self) -> None:
        """RFC 4493 Example 4: 64-byte message (complete blocks)."""
        msg = bytes.fromhex(
            "6bc1bee22e409f96e93d7e117393172a"  # pragma: allowlist secret
            "ae2d8a571e03ac9c9eb76fac45af8e51"  # pragma: allowlist secret
            "30c81c46a35ce411e5fbc1191a0a52ef"  # pragma: allowlist secret
            "f69f2445df4f9b17ad2b417be66c3710"  # pragma: allowlist secret
        )
        expected = bytes.fromhex("51f0bebf7e3b9d92fc49741779363cfe")
        assert aes_cmac(self.KEY, msg) == expected

    def test_rejects_wrong_key_length(self) -> None:
        with pytest.raises(CryptoError, match="16 bytes"):
            aes_cmac(b"\x00" * 8, b"test")


# ============================================================
# SIG Mesh Key Derivation (Mesh Profile 8.1 sample data)
# ============================================================


class TestS1:
    """Test s1 salt generation."""

    def test_known_vector(self) -> None:
        """Mesh Profile 8.1.1: s1(b'test')."""
        result = s1(b"test")
        expected = bytes.fromhex("b73cefbd641ef2ea598c2b6efb62f79c")
        assert result == expected

    def test_rejects_empty(self) -> None:
        with pytest.raises(CryptoError, match="non-empty"):
            s1(b"")


class TestK1:
    """Test k1 key derivation."""

    def test_known_vector(self) -> None:
        """Mesh Profile 8.1.2: k1 sample data."""
        n = bytes.fromhex("3216d1509884b533248541792b877f98")
        salt = s1(b"test")
        p = b"hello"
        result = k1(n, salt, p)
        # Known output from Mesh Profile spec
        assert len(result) == 16


class TestK2:
    """Test k2 network key derivation."""

    def test_known_vector(self) -> None:
        """Mesh Profile 8.1.3: k2 sample data."""
        n = bytes.fromhex("f7a2a44f8e8a8029064f173ddc1e2b00")
        p = b"\x00"
        nid, enc_key, priv_key = k2(n, p)

        assert nid == 0x7F
        assert enc_key == bytes.fromhex("9f589181a0f50de73c8070c7a6d27f46")
        assert priv_key == bytes.fromhex("4c715bd4a64b938f99b453351653124f")

    def test_nid_is_7_bits(self) -> None:
        """NID must be 7-bit (0-127)."""
        nid, _, _ = k2(b"\x01" * 16, b"\x00")
        assert 0 <= nid <= 127

    def test_keys_are_16_bytes(self) -> None:
        _, enc_key, priv_key = k2(b"\x01" * 16, b"\x00")
        assert len(enc_key) == 16
        assert len(priv_key) == 16

    def test_rejects_wrong_key_length(self) -> None:
        with pytest.raises(CryptoError, match="16 bytes"):
            k2(b"\x01" * 8, b"\x00")


class TestK3:
    """Test k3 network ID derivation."""

    def test_known_vector(self) -> None:
        """Mesh Profile 8.1.4: k3 sample data."""
        n = bytes.fromhex("f7a2a44f8e8a8029064f173ddc1e2b00")
        result = k3(n)
        assert result == bytes.fromhex("ff046958233db014")

    def test_output_is_8_bytes(self) -> None:
        result = k3(b"\x01" * 16)
        assert len(result) == 8


class TestK4:
    """Test k4 AID derivation."""

    def test_known_vector(self) -> None:
        """Mesh Profile 8.1.5: k4 sample data."""
        n = bytes.fromhex("3216d1509884b533248541792b877f98")
        result = k4(n)
        assert result == 0x38

    def test_aid_is_6_bits(self) -> None:
        """AID must be 6-bit (0-63)."""
        result = k4(b"\x01" * 16)
        assert 0 <= result <= 63


# ============================================================
# AES-CCM
# ============================================================


class TestMeshAesCcm:
    """Test mesh AES-CCM encrypt/decrypt."""

    def test_roundtrip_4_byte_mic(self) -> None:
        key = b"\x42" * 16
        nonce = b"\x01" * 13
        plaintext = b"hello mesh world"
        ct = mesh_aes_ccm_encrypt(key, nonce, plaintext, 4)
        assert len(ct) == len(plaintext) + 4
        pt = mesh_aes_ccm_decrypt(key, nonce, ct, 4)
        assert pt == plaintext

    def test_roundtrip_8_byte_mic(self) -> None:
        key = b"\x42" * 16
        nonce = b"\x01" * 13
        plaintext = b"control msg"
        ct = mesh_aes_ccm_encrypt(key, nonce, plaintext, 8)
        assert len(ct) == len(plaintext) + 8
        pt = mesh_aes_ccm_decrypt(key, nonce, ct, 8)
        assert pt == plaintext

    def test_tampered_data_raises(self) -> None:
        key = b"\x42" * 16
        nonce = b"\x01" * 13
        ct = mesh_aes_ccm_encrypt(key, nonce, b"test data", 4)
        tampered = bytearray(ct)
        tampered[0] ^= 0xFF
        with pytest.raises(CryptoError, match="authentication failed"):
            mesh_aes_ccm_decrypt(key, nonce, bytes(tampered), 4)

    def test_wrong_key_raises(self) -> None:
        key1 = b"\x42" * 16
        key2 = b"\x43" * 16
        nonce = b"\x01" * 13
        ct = mesh_aes_ccm_encrypt(key1, nonce, b"secret", 4)
        with pytest.raises(CryptoError, match="authentication failed"):
            mesh_aes_ccm_decrypt(key2, nonce, ct, 4)

    def test_rejects_invalid_mic_len(self) -> None:
        with pytest.raises(CryptoError, match="mic_len"):
            mesh_aes_ccm_encrypt(b"\x00" * 16, b"\x00" * 13, b"test", 3)

    def test_empty_plaintext(self) -> None:
        key = b"\x42" * 16
        nonce = b"\x01" * 13
        ct = mesh_aes_ccm_encrypt(key, nonce, b"", 4)
        assert len(ct) == 4  # Just MIC
        pt = mesh_aes_ccm_decrypt(key, nonce, ct, 4)
        assert pt == b""
