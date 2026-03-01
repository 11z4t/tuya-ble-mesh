"""Telink BLE Mesh cryptographic operations.

This is the ONLY module that performs cryptographic operations (Rule S4).
All functions use the Telink byte-reversal convention for AES-ECB.

SECURITY: Key material is NEVER logged, printed, or included in
exception messages. Only lengths and operation names are safe to log.

JUSTIFICATION for AES-ECB: Telink BLE Mesh uses AES-128-ECB for
single-block operations (session key derivation, pair proof). This is
a single-block operation where ECB == raw AES. CTR mode and CBC-MAC
are constructed manually on top of ECB to implement AES-CCM, matching
the Telink SDK and python-awox-mesh-light reference implementation.
"""

import hmac
import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from tuya_ble_mesh.exceptions import AuthenticationError, CryptoError

# --- Constants ---

_AES_BLOCK_SIZE = 16
_SESSION_RANDOM_SIZE = 8
_MAC_SIZE = 2  # Truncated CBC-MAC


def _validate_key(key: bytes) -> None:
    """Raise CryptoError if key is not 16 bytes."""
    if len(key) != _AES_BLOCK_SIZE:
        msg = f"Key must be {_AES_BLOCK_SIZE} bytes, got {len(key)}"
        raise CryptoError(msg)


def _pad_to_block(data: bytes) -> bytes:
    """Null-pad data to 16-byte AES block boundary."""
    if len(data) >= _AES_BLOCK_SIZE:
        return data[:_AES_BLOCK_SIZE]
    return data + b"\x00" * (_AES_BLOCK_SIZE - len(data))


# --- Core AES primitive ---


def telink_aes_encrypt(key: bytes, plaintext: bytes) -> bytes:
    """AES-128-ECB encrypt with Telink byte-reversal convention.

    Telink BLE mesh chips use little-endian byte ordering for AES.
    Both key and plaintext are reversed before encryption, and the
    ciphertext is reversed after. This matches the Telink SDK.

    Args:
        key: 16-byte AES key.
        plaintext: Data to encrypt (padded to 16 bytes).

    Returns:
        16-byte ciphertext with Telink byte ordering.
    """
    _validate_key(key)
    padded = _pad_to_block(plaintext)

    k = bytearray(key)
    k.reverse()
    v = bytearray(padded)
    v.reverse()

    cipher = Cipher(algorithms.AES(bytes(k)), modes.ECB())  # nosec B305
    encryptor = cipher.encryptor()
    result = bytearray(encryptor.update(bytes(v)) + encryptor.finalize())
    result.reverse()
    return bytes(result)


# --- Session key and pairing ---


def generate_session_random() -> bytes:
    """Generate 8 cryptographically random bytes for pairing handshake.

    Returns:
        8 random bytes for the session random exchange.
    """
    return os.urandom(_SESSION_RANDOM_SIZE)


def _xor_name_password(mesh_name: bytes, mesh_password: bytes) -> bytes:
    """XOR mesh name with password (both null-padded to 16 bytes)."""
    name_padded = _pad_to_block(mesh_name)
    pass_padded = _pad_to_block(mesh_password)
    return bytes(a ^ b for a, b in zip(name_padded, pass_padded, strict=True))


def make_pair_packet(
    mesh_name: bytes,
    mesh_password: bytes,
    session_random: bytes,
) -> bytes:
    """Build the 17-byte pair request packet for characteristic 1914.

    Packet format: [0x0C][8B session_random][8B encrypted proof]

    The proof is AES-ECB(key=session_random_padded, pt=name_XOR_pass),
    truncated to the first 8 bytes.

    Args:
        mesh_name: Mesh network name (e.g. b"out_of_mesh").
        mesh_password: Mesh network password (e.g. b"123456").
        session_random: 8 random bytes from generate_session_random().

    Returns:
        17-byte pair request packet.
    """
    if len(session_random) != _SESSION_RANDOM_SIZE:
        msg = f"Session random must be {_SESSION_RANDOM_SIZE} bytes, got {len(session_random)}"
        raise CryptoError(msg)

    name_pass = _xor_name_password(mesh_name, mesh_password)
    random_key = _pad_to_block(session_random)
    encrypted = telink_aes_encrypt(random_key, name_pass)

    return b"\x0c" + session_random + encrypted[:_SESSION_RANDOM_SIZE]


def make_session_key(
    mesh_name: bytes,
    mesh_password: bytes,
    client_random: bytes,
    device_random: bytes,
) -> bytes:
    """Derive the 16-byte session key from handshake random values.

    Algorithm: AES-ECB(key=name_XOR_pass, plaintext=client_rand || device_rand)

    Args:
        mesh_name: Mesh network name.
        mesh_password: Mesh network password.
        client_random: 8 bytes sent by controller in pair packet.
        device_random: 8 bytes received from device in pair response.

    Returns:
        16-byte session key.
    """
    if len(client_random) != _SESSION_RANDOM_SIZE:
        msg = f"Client random must be {_SESSION_RANDOM_SIZE} bytes, got {len(client_random)}"
        raise CryptoError(msg)
    if len(device_random) != _SESSION_RANDOM_SIZE:
        msg = f"Device random must be {_SESSION_RANDOM_SIZE} bytes, got {len(device_random)}"
        raise CryptoError(msg)

    name_pass = _xor_name_password(mesh_name, mesh_password)
    combined = client_random + device_random  # 16 bytes total
    return telink_aes_encrypt(name_pass, combined)


def encrypt_mesh_credential(session_key: bytes, data: bytes) -> bytes:
    """Encrypt a mesh credential (name, password, or LTK) with session key.

    Used during set-mesh-credentials phase (opcodes 0x04, 0x05, 0x06).
    Produces a 17-byte packet: [opcode][16B encrypted data].
    Caller must prepend the opcode byte.

    Args:
        session_key: 16-byte session key from make_session_key().
        data: Credential to encrypt (padded to 16 bytes).

    Returns:
        16-byte encrypted credential.
    """
    _validate_key(session_key)
    padded = _pad_to_block(data)
    return telink_aes_encrypt(session_key, padded)


# --- CTR mode encryption (for command packets) ---


def crypt_payload(key: bytes, nonce: bytes, payload: bytes) -> bytes:
    """Encrypt or decrypt payload using manual CTR mode on AES-ECB.

    CTR mode is symmetric: the same function encrypts and decrypts.

    Args:
        key: 16-byte encryption key.
        nonce: 8-byte nonce (padded to 15 bytes for counter block).
        payload: Data to encrypt or decrypt.

    Returns:
        Encrypted or decrypted data (same length as payload).
    """
    _validate_key(key)
    base = bytearray(b"\x00" + nonce)
    base = bytearray(_pad_to_block(bytes(base)))
    result = bytearray()

    for i in range(0, len(payload), _AES_BLOCK_SIZE):
        keystream = telink_aes_encrypt(key, bytes(base))
        chunk = payload[i : i + _AES_BLOCK_SIZE]
        result.extend(a ^ b for a, b in zip(keystream[: len(chunk)], chunk, strict=True))
        base[0] = (base[0] + 1) & 0xFF

    return bytes(result)


# --- CBC-MAC (for command packet integrity) ---


def make_checksum(key: bytes, nonce: bytes, payload: bytes) -> bytes:
    """Compute CBC-MAC checksum for packet integrity.

    Returns the full 16-byte MAC. Caller truncates to 2 bytes for the
    wire format.

    Args:
        key: 16-byte encryption key.
        nonce: 8-byte nonce.
        payload: Data to authenticate.

    Returns:
        16-byte CBC-MAC (caller truncates to 2 bytes for wire format).
    """
    _validate_key(key)
    base = bytearray(nonce) + bytearray([len(payload)])
    base_padded = _pad_to_block(bytes(base))
    check = bytearray(telink_aes_encrypt(key, base_padded))

    for i in range(0, len(payload), _AES_BLOCK_SIZE):
        chunk = _pad_to_block(payload[i : i + _AES_BLOCK_SIZE])
        xored = bytes(a ^ b for a, b in zip(check, chunk, strict=True))
        check = bytearray(telink_aes_encrypt(key, xored))

    return bytes(check)


def verify_checksum(key: bytes, nonce: bytes, payload: bytes, expected_mac: bytes) -> bool:
    """Verify a 2-byte truncated CBC-MAC.

    Uses constant-time comparison via hmac.compare_digest.

    Args:
        key: 16-byte encryption key.
        nonce: 8-byte nonce.
        payload: Data that was authenticated.
        expected_mac: 2-byte MAC from the packet.

    Returns:
        True if the MAC is valid.

    Raises:
        AuthenticationError: If the MAC does not match.
    """
    computed = make_checksum(key, nonce, payload)
    if not hmac.compare_digest(computed[:_MAC_SIZE], expected_mac[:_MAC_SIZE]):
        raise AuthenticationError("Packet MAC verification failed")
    return True
