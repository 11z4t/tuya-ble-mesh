"""SIG Mesh cryptographic primitives.

Implements the key derivation and encryption functions defined in the
Bluetooth Mesh Profile Specification (Section 3.8):

- AES-CMAC (RFC 4493)
- Salt generation (s1)
- Key derivation: k1, k2, k3, k4
- AES-CCM encrypt/decrypt (network + transport layers)

This module complements ``crypto.py`` (Telink proprietary) with standard
SIG Mesh crypto. Rule S4: all crypto operations in crypto modules only.

SECURITY: Key material is NEVER logged, printed, or included in
exception messages. Only lengths and operation names are safe to log.
"""

from __future__ import annotations

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESCCM

from tuya_ble_mesh.exceptions import CryptoError

_AES_BLOCK_SIZE = 16
_CMAC_RB = b"\x00" * 15 + b"\x87"  # CMAC constant Rb


def _validate_key(key: bytes) -> None:
    """Raise CryptoError if key is not 16 bytes."""
    if len(key) != _AES_BLOCK_SIZE:
        msg = f"Key must be {_AES_BLOCK_SIZE} bytes, got {len(key)}"
        raise CryptoError(msg)


# ============================================================
# AES-ECB (single block, standard byte order)
# ============================================================


def aes_ecb(key: bytes, plaintext: bytes) -> bytes:
    """AES-128-ECB encrypt a single 16-byte block (standard byte order).

    Unlike ``crypto.telink_aes_encrypt``, this uses standard byte ordering
    (no Telink reversal) as required by the SIG Mesh specification.

    Args:
        key: 16-byte AES key.
        plaintext: 16-byte plaintext block.

    Returns:
        16-byte ciphertext.
    """
    _validate_key(key)
    if len(plaintext) != _AES_BLOCK_SIZE:
        msg = f"Plaintext must be {_AES_BLOCK_SIZE} bytes, got {len(plaintext)}"
        raise CryptoError(msg)
    cipher = Cipher(algorithms.AES(key), modes.ECB())  # nosec B305
    enc = cipher.encryptor()
    return enc.update(plaintext) + enc.finalize()


# ============================================================
# AES-CMAC (RFC 4493)
# ============================================================


def _xor(a: bytes, b: bytes) -> bytes:
    """XOR two equal-length byte strings."""
    return bytes(x ^ y for x, y in zip(a, b, strict=True))


def _shift_left(data: bytes) -> bytes:
    """Left-shift a 16-byte block by one bit."""
    result = bytearray(_AES_BLOCK_SIZE)
    for i in range(_AES_BLOCK_SIZE - 1):
        result[i] = ((data[i] << 1) & 0xFF) | (data[i + 1] >> 7)
    result[_AES_BLOCK_SIZE - 1] = (data[_AES_BLOCK_SIZE - 1] << 1) & 0xFF
    return bytes(result)


def aes_cmac(key: bytes, msg: bytes) -> bytes:
    """Compute AES-CMAC (RFC 4493) over a message.

    Args:
        key: 16-byte AES key.
        msg: Message of any length.

    Returns:
        16-byte MAC.
    """
    _validate_key(key)

    # Sub-key generation (RFC 4493 Section 2.3)
    zero = b"\x00" * _AES_BLOCK_SIZE
    l_val = aes_ecb(key, zero)

    k1 = _shift_left(l_val)
    if l_val[0] & 0x80:
        k1 = _xor(k1, _CMAC_RB)

    k2 = _shift_left(k1)
    if k1[0] & 0x80:
        k2 = _xor(k2, _CMAC_RB)

    # MAC generation (RFC 4493 Section 2.4)
    n = max(1, (len(msg) + _AES_BLOCK_SIZE - 1) // _AES_BLOCK_SIZE)
    complete_block = len(msg) > 0 and len(msg) % _AES_BLOCK_SIZE == 0

    if complete_block:
        m_last = _xor(msg[(n - 1) * _AES_BLOCK_SIZE :], k1)
    else:
        # Pad with 0x80 || 0x00...
        tail = msg[(n - 1) * _AES_BLOCK_SIZE :]
        padded = tail + b"\x80" + b"\x00" * (_AES_BLOCK_SIZE - 1 - len(tail))
        m_last = _xor(padded[:_AES_BLOCK_SIZE], k2)

    x = b"\x00" * _AES_BLOCK_SIZE
    for i in range(n - 1):
        block = msg[i * _AES_BLOCK_SIZE : (i + 1) * _AES_BLOCK_SIZE]
        x = aes_ecb(key, _xor(x, block))
    return aes_ecb(key, _xor(x, m_last))


# ============================================================
# SIG Mesh Key Derivation Functions (Mesh Profile 3.8.2)
# ============================================================


def s1(m: bytes) -> bytes:
    """Salt generation function (Mesh Profile 3.8.2.4).

    Args:
        m: Non-zero-length input.

    Returns:
        16-byte salt value.
    """
    if not m:
        msg = "s1 input must be non-empty"
        raise CryptoError(msg)
    return aes_cmac(b"\x00" * _AES_BLOCK_SIZE, m)


def k1(n: bytes, salt: bytes, p: bytes) -> bytes:
    """Key derivation function k1 (Mesh Profile 3.8.2.5).

    Args:
        n: Input key material (N).
        salt: 16-byte salt from s1().
        p: Additional info (P).

    Returns:
        16-byte derived key.
    """
    _validate_key(salt)
    t = aes_cmac(salt, n)
    return aes_cmac(t, p)


def k2(n: bytes, p: bytes) -> tuple[int, bytes, bytes]:
    """Network key derivation k2 (Mesh Profile 3.8.2.6).

    Derives NID, encryption key, and privacy key from a network key.

    Args:
        n: 16-byte network key.
        p: Additional data (typically ``b"\\x00"``).

    Returns:
        Tuple of (NID as 7-bit int, 16-byte encryption key, 16-byte privacy key).
    """
    _validate_key(n)
    salt = s1(b"smk2")
    t = aes_cmac(salt, n)
    t1 = aes_cmac(t, p + b"\x01")
    t2 = aes_cmac(t, t1 + p + b"\x02")
    t3 = aes_cmac(t, t2 + p + b"\x03")
    nid = t1[15] & 0x7F
    return nid, t2, t3


def k3(n: bytes) -> bytes:
    """Network ID derivation k3 (Mesh Profile 3.8.2.7).

    Args:
        n: 16-byte network key.

    Returns:
        8-byte network ID.
    """
    _validate_key(n)
    salt = s1(b"smk3")
    t = aes_cmac(salt, n)
    return aes_cmac(t, b"id64\x01")[8:]


def k4(n: bytes) -> int:
    """AID derivation k4 (Mesh Profile 3.8.2.8).

    Args:
        n: 16-byte application key.

    Returns:
        6-bit AID value.
    """
    _validate_key(n)
    salt = s1(b"smk4")
    t = aes_cmac(salt, n)
    return aes_cmac(t, b"id6\x01")[15] & 0x3F


# ============================================================
# AES-CCM (Mesh Network + Transport layers)
# ============================================================


def mesh_aes_ccm_encrypt(
    key: bytes,
    nonce: bytes,
    plaintext: bytes,
    mic_len: int = 4,
) -> bytes:
    """AES-CCM encrypt for SIG Mesh (MIC appended to ciphertext).

    Args:
        key: 16-byte encryption key.
        nonce: 13-byte nonce.
        plaintext: Data to encrypt.
        mic_len: MIC length in bytes (4 for access, 8 for control).

    Returns:
        Ciphertext with MIC appended.
    """
    _validate_key(key)
    if mic_len not in (4, 8):
        msg = f"mic_len must be 4 or 8, got {mic_len}"
        raise CryptoError(msg)
    aesccm = AESCCM(key, tag_length=mic_len)
    return aesccm.encrypt(nonce, plaintext, b"")


def mesh_aes_ccm_decrypt(
    key: bytes,
    nonce: bytes,
    ct_and_mic: bytes,
    mic_len: int = 4,
) -> bytes:
    """AES-CCM decrypt for SIG Mesh.

    Args:
        key: 16-byte encryption key.
        nonce: 13-byte nonce.
        ct_and_mic: Ciphertext with MIC appended.
        mic_len: MIC length in bytes (4 for access, 8 for control).

    Returns:
        Decrypted plaintext.

    Raises:
        CryptoError: If authentication fails.
    """
    _validate_key(key)
    if mic_len not in (4, 8):
        msg = f"mic_len must be 4 or 8, got {mic_len}"
        raise CryptoError(msg)
    aesccm = AESCCM(key, tag_length=mic_len)
    try:
        return aesccm.decrypt(nonce, ct_and_mic, b"")
    except InvalidTag as exc:
        msg = "AES-CCM authentication failed"
        raise CryptoError(msg) from exc
