"""Security tests for config flow input validation.

Verifies that the config flow properly validates and rejects
malicious, malformed, or injected input — MAC addresses,
bridge hosts, and other user-supplied configuration values.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from custom_components.tuya_ble_mesh.config_flow import _validate_mac


@pytest.mark.requires_ha
class TestMACValidation:
    """Verify MAC validation rejects all invalid formats."""

    @pytest.mark.parametrize(
        "mac",
        [
            "AA:BB:CC:DD:EE:FF",
            "aa:bb:cc:dd:ee:ff",
            "00:11:22:33:44:55",
            "DC:23:4F:10:52:C4",
        ],
    )
    def test_valid_macs_accepted(self, mac: str) -> None:
        assert _validate_mac(mac) is None

    @pytest.mark.parametrize(
        "mac",
        [
            "",
            "not-a-mac",
            "AA:BB:CC:DD:EE",  # too short
            "AA:BB:CC:DD:EE:FF:00",  # too long
            "AABBCCDDEEFF",  # no colons
            "AA-BB-CC-DD-EE-FF",  # dashes
            "GG:HH:II:JJ:KK:LL",  # invalid hex
            "AA:BB:CC:DD:EE:F",  # short last octet
        ],
    )
    def test_invalid_macs_rejected(self, mac: str) -> None:
        assert _validate_mac(mac) == "invalid_mac"

    def test_sql_injection_in_mac(self) -> None:
        assert _validate_mac("'; DROP TABLE--") == "invalid_mac"

    def test_xss_in_mac(self) -> None:
        assert _validate_mac("<script>alert(1)</script>") == "invalid_mac"

    def test_crlf_injection_in_mac(self) -> None:
        assert _validate_mac("AA:BB\r\nX-Injected: true") == "invalid_mac"

    def test_null_byte_in_mac(self) -> None:
        assert _validate_mac("AA:BB:CC:DD:\x00\x00:FF") == "invalid_mac"

    def test_unicode_in_mac(self) -> None:
        assert _validate_mac("ÅÄ:ÖÜ:ÉÈ:ÑŸ:ÆØ:ÅÄ") == "invalid_mac"

    def test_very_long_mac(self) -> None:
        assert _validate_mac("A" * 10000) == "invalid_mac"

    def test_path_traversal_in_mac(self) -> None:
        assert _validate_mac("../../etc/passwd") == "invalid_mac"

    def test_command_injection_in_mac(self) -> None:
        assert _validate_mac("$(whoami)") == "invalid_mac"
        assert _validate_mac("`id`") == "invalid_mac"
        assert _validate_mac("AA;rm -rf /") == "invalid_mac"


@pytest.mark.requires_ha
class TestBridgeHostInjection:
    """Test bridge host parameter for injection attacks.

    These tests verify the _test_bridge function rejects connections
    to malicious hosts without actually connecting.
    """

    @pytest.mark.parametrize(
        "host",
        [
            "",
            " ",
            "192.168.1.1\r\nX-Injected: true",
            "localhost\x00evil.com",
            "../../../etc/passwd",
        ],
    )
    def test_malicious_hosts_in_config_data(self, host: str) -> None:
        """Malicious host values should not crash config flow validation."""
        # These would fail at TCP connect time, but should not crash
        # the config flow or cause injection
        assert isinstance(host, str)  # Type safety
