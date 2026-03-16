"""Security tests for bridge HTTP API.

Verifies that the bridge device classes handle malformed,
injected, and oversized inputs safely. Tests the HTTP request
construction to ensure no header injection is possible.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.exceptions import (
    InvalidRequestError,
)
from tuya_ble_mesh.sig_mesh_bridge import SIGMeshBridgeDevice, TelinkBridgeDevice


class TestHTTPHeaderInjection:
    """Verify HTTP header injection is not possible via user-supplied values."""

    def test_host_with_crlf_in_bridge_url(self) -> None:
        """Bridge host with CRLF should raise InvalidRequestError (injection prevention)."""
        malicious_host = "evil.com\r\nX-Injected: true"
        with pytest.raises(InvalidRequestError, match="contains CRLF characters"):
            SIGMeshBridgeDevice(
                "AA:BB:CC:DD:EE:FF",
                target_addr=0x00B0,
                bridge_host=malicious_host,
            )

    def test_telink_host_with_crlf(self) -> None:
        """Telink bridge host with CRLF injection attempt should raise InvalidRequestError."""
        malicious_host = "192.168.1.1\r\nX-Evil: pwned"
        with pytest.raises(InvalidRequestError, match="contains CRLF characters"):
            TelinkBridgeDevice(
                "AA:BB:CC:DD:EE:FF",
                bridge_host=malicious_host,
            )


class TestMACAddressNormalization:
    """Verify MAC addresses are uppercased and stored safely."""

    def test_mac_uppercased_sig_bridge(self) -> None:
        dev = SIGMeshBridgeDevice("aa:bb:cc:dd:ee:ff", 0x00B0, "localhost")
        assert dev.address == "AA:BB:CC:DD:EE:FF"

    def test_mac_uppercased_telink_bridge(self) -> None:
        dev = TelinkBridgeDevice("aa:bb:cc:dd:ee:ff", "localhost")
        assert dev.address == "AA:BB:CC:DD:EE:FF"

    def test_mac_with_injection_attempt(self) -> None:
        """MAC containing special chars should be stored as-is (uppercased)."""
        malicious_mac = 'AA:BB"; DROP TABLE--'
        dev = SIGMeshBridgeDevice(malicious_mac, 0x00B0, "localhost")
        assert dev.address == malicious_mac.upper()


class TestBridgeDeviceInitBounds:
    """Verify bridge device constructors handle edge cases."""

    def test_negative_port(self) -> None:
        """Negative port number should be stored (OS will reject on connect)."""
        dev = SIGMeshBridgeDevice("AA:BB:CC:DD:EE:FF", 0x00B0, "localhost", -1)
        assert dev._bridge_port == -1

    def test_zero_target_addr(self) -> None:
        dev = SIGMeshBridgeDevice("AA:BB:CC:DD:EE:FF", 0x0000, "localhost")
        assert dev._target_addr == 0x0000

    def test_max_target_addr(self) -> None:
        dev = SIGMeshBridgeDevice("AA:BB:CC:DD:EE:FF", 0xFFFF, "localhost")
        assert dev._target_addr == 0xFFFF

    def test_empty_host(self) -> None:
        dev = SIGMeshBridgeDevice("AA:BB:CC:DD:EE:FF", 0x00B0, "")
        assert dev._bridge_host == ""
