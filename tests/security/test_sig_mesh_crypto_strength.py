"""SIG Mesh crypto strength tests.

Verifies key derivation output lengths, AES-CCM input validation,
key material protection in exceptions, and MeshKeys validation.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from tuya_ble_mesh.exceptions import CryptoError
from tuya_ble_mesh.sig_mesh_crypto import (
    _validate_key,
    k2,
    k3,
    k4,
    mesh_aes_ccm_decrypt,
    mesh_aes_ccm_encrypt,
)
from tuya_ble_mesh.sig_mesh_protocol import MeshKeys

_VALID_KEY = b"\x00" * 16
_VALID_NONCE = b"\x00" * 13


class TestKeyDerivationOutputLengths:
    """Verify k2/k3/k4 output lengths match SIG Mesh spec."""

    def test_k2_returns_nid_enc_priv(self) -> None:
        """k2 should return (7-bit NID, 16B enc_key, 16B priv_key)."""
        nid, enc, priv = k2(_VALID_KEY, b"\x00")
        assert 0 <= nid <= 0x7F
        assert len(enc) == 16
        assert len(priv) == 16

    def test_k3_returns_8_bytes(self) -> None:
        """k3 should return 8-byte network ID."""
        result = k3(_VALID_KEY)
        assert len(result) == 8

    def test_k4_returns_6_bit(self) -> None:
        """k4 should return 6-bit AID."""
        result = k4(_VALID_KEY)
        assert 0 <= result <= 0x3F


class TestAESCCMInputValidation:
    """Verify AES-CCM rejects invalid key/nonce lengths."""

    @pytest.mark.parametrize("key_len", [4, 8, 32])
    def test_encrypt_rejects_wrong_key_length(self, key_len: int) -> None:
        bad_key = b"\x00" * key_len
        with pytest.raises(CryptoError, match="16 bytes"):
            mesh_aes_ccm_encrypt(bad_key, _VALID_NONCE, b"test")

    @pytest.mark.parametrize("key_len", [4, 8, 32])
    def test_decrypt_rejects_wrong_key_length(self, key_len: int) -> None:
        bad_key = b"\x00" * key_len
        # Need valid ciphertext + MIC (at least mic_len bytes)
        ct = b"\x00" * 8
        with pytest.raises(CryptoError, match="16 bytes"):
            mesh_aes_ccm_decrypt(bad_key, _VALID_NONCE, ct)

    def test_encrypt_rejects_invalid_mic_len(self) -> None:
        with pytest.raises(CryptoError, match="mic_len"):
            mesh_aes_ccm_encrypt(_VALID_KEY, _VALID_NONCE, b"test", mic_len=6)

    def test_decrypt_rejects_invalid_mic_len(self) -> None:
        with pytest.raises(CryptoError, match="mic_len"):
            mesh_aes_ccm_decrypt(_VALID_KEY, _VALID_NONCE, b"\x00" * 8, mic_len=3)


class TestValidateKey:
    """Verify _validate_key enforces 16 bytes."""

    def test_valid_key_accepted(self) -> None:
        _validate_key(_VALID_KEY)  # Should not raise

    @pytest.mark.parametrize("key_len", [0, 1, 8, 15, 17, 32])
    def test_invalid_key_rejected(self, key_len: int) -> None:
        with pytest.raises(CryptoError, match="16 bytes"):
            _validate_key(b"\x00" * key_len)


class TestNoKeyMaterialInExceptions:
    """Verify exception messages do not contain key bytes."""

    def test_short_key_no_leakage(self) -> None:
        bad_key = b"\xde\xad\xbe\xef"
        try:
            mesh_aes_ccm_encrypt(bad_key, _VALID_NONCE, b"test")
        except CryptoError as exc:
            msg = str(exc).lower()
            assert "dead" not in msg
            assert "beef" not in msg
            assert "\\xde" not in msg

    def test_wrong_nonce_decrypt_no_key_leakage(self) -> None:
        """AES-CCM auth failure should not leak key material."""
        ct = mesh_aes_ccm_encrypt(_VALID_KEY, _VALID_NONCE, b"hello")
        wrong_nonce = b"\xff" * 13
        try:
            mesh_aes_ccm_decrypt(_VALID_KEY, wrong_nonce, ct)
        except CryptoError as exc:
            msg = str(exc)
            # Should say "authentication failed", not include key bytes
            assert "authentication" in msg.lower() or "failed" in msg.lower()
            assert "\\x00" * 8 not in msg  # Don't leak the all-zeros key


class TestMeshKeysValidation:
    """Verify MeshKeys rejects invalid hex inputs."""

    def test_rejects_odd_length_hex(self) -> None:
        with pytest.raises(ValueError):
            MeshKeys(
                "f7a2a44f8e8a8029064f173ddc1e2b0",  # 31 chars (odd)  # pragma: allowlist secret
                "00112233445566778899aabbccddeeff",  # pragma: allowlist secret
                "3216d1509884b533248541792b877f98",  # pragma: allowlist secret
            )

    def test_rejects_non_hex_chars(self) -> None:
        with pytest.raises(ValueError):
            MeshKeys(
                "f7a2a44f8e8a8029064f173ddc1e2bXX",  # XX = invalid  # pragma: allowlist secret
                "00112233445566778899aabbccddeeff",  # pragma: allowlist secret
                "3216d1509884b533248541792b877f98",  # pragma: allowlist secret
            )

    def test_rejects_wrong_key_length_in_derivation(self) -> None:
        with pytest.raises(CryptoError):
            MeshKeys(
                "0011223344556677",  # 8 bytes, need 16
                "00112233445566778899aabbccddeeff",  # pragma: allowlist secret
                "3216d1509884b533248541792b877f98",  # pragma: allowlist secret
            )

    def test_valid_keys_accepted(self) -> None:
        keys = MeshKeys(
            "f7a2a44f8e8a8029064f173ddc1e2b00",  # pragma: allowlist secret
            "00112233445566778899aabbccddeeff",  # pragma: allowlist secret
            "3216d1509884b533248541792b877f98",  # pragma: allowlist secret
        )
        assert keys.nid is not None
        assert keys.aid is not None
