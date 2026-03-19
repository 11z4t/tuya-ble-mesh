"""Secret leakage prevention tests.

Verifies that secret material (keys, passwords, tokens) is never
exposed in exception messages, log output, or string representations.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(
        Path(__file__).resolve().parent.parent.parent
        / "custom_components"
        / "tuya_ble_mesh"
        / "lib"
    ),
)

from tuya_ble_mesh.crypto import (
    make_pair_packet,
    make_session_key,
    telink_aes_encrypt,
)
from tuya_ble_mesh.exceptions import CryptoError, SecretAccessError
from tuya_ble_mesh.secrets import DictSecretsManager, SecretsManager

_LIB_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "custom_components"
    / "tuya_ble_mesh"
    / "lib"
    / "tuya_ble_mesh"
)


class TestSecretManagerLeakage:
    """Verify SecretsManager never exposes secret values."""

    @pytest.mark.asyncio
    async def test_missing_key_error_no_secret_value(self) -> None:
        """DictSecretsManager error should not contain secret values."""
        secrets = {
            "mesh-key/password": "super_secret_key_12345",  # pragma: allowlist secret
        }
        mgr = DictSecretsManager(secrets)
        try:
            await mgr.get("wrong-item", "password")
        except SecretAccessError as exc:
            msg = str(exc)
            assert "super_secret" not in msg
            assert "12345" not in msg

    @pytest.mark.asyncio
    async def test_dict_manager_no_repr_leak(self) -> None:
        """DictSecretsManager repr/str should not expose secrets."""
        secrets = {
            "key/password": "my_secret_value_xyz",  # pragma: allowlist secret
        }
        mgr = DictSecretsManager(secrets)
        assert "my_secret_value" not in repr(mgr)
        assert "my_secret_value" not in str(mgr)

    def test_op_unavailable_error_no_secrets(self) -> None:
        """SecretsManager should report 'op not installed', not secrets."""
        mgr = SecretsManager()
        try:
            asyncio.get_event_loop().run_until_complete(mgr.get("test"))
        except SecretAccessError as exc:
            msg = str(exc)
            assert "op" in msg.lower() or "1password" in msg.lower()
        except RuntimeError:
            pass  # No event loop — acceptable


class TestCryptoExceptionLeakage:
    """Verify crypto exceptions never contain key material."""

    def test_aes_short_key_no_leak(self) -> None:
        bad_key = bytes.fromhex("deadbeefcafebabe")
        try:
            telink_aes_encrypt(bad_key, b"\x00" * 16)
        except CryptoError as exc:
            msg = str(exc).lower()
            assert "deadbeef" not in msg
            assert "cafebabe" not in msg

    def test_session_key_error_no_leak(self) -> None:
        """Session key derivation errors should not expose inputs."""
        try:
            make_session_key(b"name", b"pass", b"\xab" * 2, b"\xcd" * 8)
        except CryptoError as exc:
            msg = str(exc)
            assert "\\xab" not in msg
            assert "name" not in msg.lower() or "item" in msg.lower()

    def test_pair_packet_error_no_leak(self) -> None:
        """Pair packet errors should not expose credentials."""
        try:
            # Short random triggers CryptoError
            make_pair_packet(
                b"mesh_name",
                b"mesh_password",  # pragma: allowlist secret
                b"\x01" * 2,
            )
        except CryptoError as exc:
            msg = str(exc)
            assert "mesh_name" not in msg
            assert "mesh_password" not in msg


class TestLogRedaction:
    """Verify logging calls use [REDACTED] for sensitive data."""

    def test_secrets_module_uses_redacted_in_logs(self) -> None:
        """Check that secrets.py log messages contain [REDACTED]."""
        content = (_LIB_DIR / "secrets.py").read_text()
        log_lines = [line for line in content.splitlines() if "_LOGGER." in line]
        for line in log_lines:
            if "secret" in line.lower() or "key" in line.lower():
                assert "REDACTED" in line, f"Log line may leak secrets: {line.strip()}"

    def test_crypto_module_no_key_logging(self) -> None:
        """Check that crypto.py never logs key values."""
        content = (_LIB_DIR / "crypto.py").read_text()
        log_lines = [line.strip() for line in content.splitlines() if "_LOGGER." in line]
        for line in log_lines:
            assert ".hex()" not in line, f"Key hex logged: {line}"

    def test_bridge_module_no_secret_logging(self) -> None:
        """Check that sig_mesh_bridge.py logs no secret material."""
        content = (_LIB_DIR / "sig_mesh_bridge.py").read_text()
        log_lines = [line.strip() for line in content.splitlines() if "_LOGGER." in line]
        for line in log_lines:
            assert "key" not in line.lower() or "REDACTED" in line, (
                f"Potential key leak in log: {line}"
            )
            low = line.lower()
            assert "password" not in low, f"Password in log: {line}"
            assert "token" not in low, f"Token in log: {line}"


class TestNoSysPathManipulation:
    """Verify that no code in custom_components/ manipulates sys.path (PLAT-741)."""

    def test_no_sys_path_manipulation(self) -> None:
        """Grep all custom_components files and verify sys.path.insert is not present.

        PLAT-741: Regression guard to prevent sys.path.insert in production code.
        This is a code smell that indicates improper import structure.
        Test files are allowed to use sys.path.insert for testing purposes.
        """
        # Path to custom_components directory
        cc_dir = Path(__file__).resolve().parent.parent.parent / "custom_components"

        # Grep all Python files in custom_components/
        violations = []
        for py_file in cc_dir.rglob("*.py"):
            content = py_file.read_text()
            for line_num, line in enumerate(content.splitlines(), start=1):
                if "sys.path.insert" in line or "sys.path.append" in line:
                    violations.append(
                        f"{py_file.relative_to(cc_dir.parent)}:{line_num}: {line.strip()}"
                    )

        assert not violations, (
            "Found sys.path manipulation in custom_components/ (forbidden):\n"
            + "\n".join(violations)
        )
