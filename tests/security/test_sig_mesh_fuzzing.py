"""SIG Mesh fuzzing tests.

Verifies that protocol parsers and crypto functions handle
random/malformed input without crashing. Only TuyaBLEMeshError
(or subclasses) should be raised — never bare exceptions.
"""

from __future__ import annotations

import contextlib
import os
import sys
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

from tuya_ble_mesh.exceptions import TuyaBLEMeshError
from tuya_ble_mesh.sig_mesh_protocol import (
    MeshKeys,
    decrypt_network_pdu,
    parse_access_opcode,
    parse_composition_data,
    parse_proxy_pdu,
    parse_segment_header,
    parse_tuya_vendor_dps,
)

# Number of fuzz iterations — keep high for CI but allow override
_FUZZ_ITERATIONS = int(os.environ.get("FUZZ_ITERATIONS", "10000"))

# Valid keys for decrypt tests
_NET_KEY_HEX = "f7a2a44f8e8a8029064f173ddc1e2b00"  # pragma: allowlist secret
_DEV_KEY_HEX = "00112233445566778899aabbccddeeff"  # pragma: allowlist secret
_APP_KEY_HEX = "3216d1509884b533248541792b877f98"  # pragma: allowlist secret


def _random_bytes(max_len: int = 64) -> bytes:
    """Generate random bytes of random length (0..max_len)."""
    length = int.from_bytes(os.urandom(1), "big") % (max_len + 1)
    return os.urandom(length)


class TestFuzzParseProxyPDU:
    """Fuzz parse_proxy_pdu with random data."""

    def test_fuzz_parse_proxy_pdu(self) -> None:
        for _ in range(_FUZZ_ITERATIONS):
            data = _random_bytes(32)
            with contextlib.suppress(TuyaBLEMeshError):
                parse_proxy_pdu(data)


class TestFuzzDecryptNetworkPDU:
    """Fuzz decrypt_network_pdu with random PDUs."""

    def test_fuzz_decrypt_network_pdu(self) -> None:
        keys = MeshKeys(_NET_KEY_HEX, _DEV_KEY_HEX, _APP_KEY_HEX)
        for _ in range(_FUZZ_ITERATIONS):
            data = _random_bytes(64)
            # Should return None or raise TuyaBLEMeshError, never crash
            with contextlib.suppress(TuyaBLEMeshError):
                result = decrypt_network_pdu(
                    keys.enc_key,
                    keys.priv_key,
                    keys.nid,
                    data,
                )
                assert result is None or hasattr(result, "transport_pdu")


class TestFuzzParseSegmentHeader:
    """Fuzz parse_segment_header with random transport PDUs."""

    def test_fuzz_parse_segment_header(self) -> None:
        for _ in range(_FUZZ_ITERATIONS):
            data = _random_bytes(20)
            with contextlib.suppress(TuyaBLEMeshError):
                parse_segment_header(data)


class TestFuzzParseTuyaVendorDPs:
    """Fuzz parse_tuya_vendor_dps with random DP streams."""

    def test_fuzz_parse_tuya_vendor_dps(self) -> None:
        for _ in range(_FUZZ_ITERATIONS):
            data = _random_bytes(128)
            result = parse_tuya_vendor_dps(data)
            assert isinstance(result, list)

    def test_fuzz_truncated_dps(self) -> None:
        """Truncated DP data should not crash."""
        for length in range(10):
            data = os.urandom(length)
            result = parse_tuya_vendor_dps(data)
            assert isinstance(result, list)


class TestFuzzParseCompositionData:
    """Fuzz parse_composition_data with random payloads."""

    def test_fuzz_parse_composition_data(self) -> None:
        for _ in range(_FUZZ_ITERATIONS):
            data = _random_bytes(64)
            with contextlib.suppress(TuyaBLEMeshError):
                parse_composition_data(data)


class TestFuzzParseAccessOpcode:
    """Fuzz parse_access_opcode with all boundary cases."""

    def test_fuzz_parse_access_opcode(self) -> None:
        for _ in range(_FUZZ_ITERATIONS):
            data = _random_bytes(8)
            try:
                opcode, params = parse_access_opcode(data)
                assert isinstance(opcode, int)
                assert isinstance(params, bytes)
            except TuyaBLEMeshError:
                pass  # Expected for empty/short data

    def test_1byte_opcode_boundary(self) -> None:
        """All 1-byte opcodes (0x00-0x7F) should parse."""
        for i in range(0x80):
            opcode, params = parse_access_opcode(bytes([i, 0x42]))
            assert opcode == i
            assert params == b"\x42"

    def test_2byte_opcode_boundary(self) -> None:
        """2-byte opcodes (0x80xx-0xBFxx) should parse."""
        for hi in range(0x80, 0xC0):
            opcode, _params = parse_access_opcode(bytes([hi, 0x01, 0xFF]))
            assert opcode == (hi << 8) | 0x01

    def test_3byte_vendor_opcode_boundary(self) -> None:
        """3-byte opcodes (0xC0xxxx-0xFFxxxx) should parse."""
        for hi in range(0xC0, 0x100):
            opcode, _params = parse_access_opcode(bytes([hi, 0x01, 0x02, 0xAA]))
            assert opcode == (hi << 16) | 0x0102
