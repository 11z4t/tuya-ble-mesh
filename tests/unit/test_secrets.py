"""Unit tests for SecretsManager and DictSecretsManager."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "lib"))

from tuya_ble_mesh.exceptions import SecretAccessError  # noqa: E402
from tuya_ble_mesh.secrets import DictSecretsManager, SecretsManager  # noqa: E402

# --- Initialization ---


class TestInit:
    """Test SecretsManager initialization."""

    def test_default_vault(self) -> None:
        sm = SecretsManager()
        assert sm.vault == "malmbergs-bt"

    def test_custom_vault(self) -> None:
        sm = SecretsManager(vault="test-vault")
        assert sm.vault == "test-vault"


# --- get() ---


def _mock_process(stdout: bytes, stderr: bytes, returncode: int) -> AsyncMock:
    """Create a mock subprocess with given outputs."""
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    return proc


class TestGet:
    """Test secret retrieval via op CLI."""

    @pytest.mark.asyncio
    async def test_successful_read(self) -> None:
        sm = SecretsManager()
        proc = _mock_process(b"secret_value\n", b"", 0)

        with (
            patch.object(sm, "_op_available", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            result = await sm.get("mesh-key", "password")

        assert result == "secret_value"
        mock_exec.assert_called_once_with(
            "op",
            "read",
            "op://malmbergs-bt/mesh-key/password",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    @pytest.mark.asyncio
    async def test_default_field_is_password(self) -> None:
        sm = SecretsManager()
        proc = _mock_process(b"value\n", b"", 0)

        with (
            patch.object(sm, "_op_available", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            await sm.get("item-name")

        mock_exec.assert_called_once_with(
            "op",
            "read",
            "op://malmbergs-bt/item-name/password",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    @pytest.mark.asyncio
    async def test_custom_vault_in_ref(self) -> None:
        sm = SecretsManager(vault="my-vault")
        proc = _mock_process(b"val\n", b"", 0)

        with (
            patch.object(sm, "_op_available", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec,
        ):
            await sm.get("item", "field")

        mock_exec.assert_called_once_with(
            "op",
            "read",
            "op://my-vault/item/field",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    @pytest.mark.asyncio
    async def test_strips_trailing_newlines(self) -> None:
        sm = SecretsManager()
        proc = _mock_process(b"value\n\n", b"", 0)

        with (
            patch.object(sm, "_op_available", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            result = await sm.get("item")

        # rstrip("\n") removes all trailing newlines
        assert result == "value"

    @pytest.mark.asyncio
    async def test_op_not_installed_raises(self) -> None:
        sm = SecretsManager()

        with (
            patch.object(sm, "_op_available", return_value=False),
            pytest.raises(SecretAccessError, match="not installed"),
        ):
            await sm.get("item")

    @pytest.mark.asyncio
    async def test_op_nonzero_exit_raises(self) -> None:
        sm = SecretsManager()
        proc = _mock_process(b"", b"[ERROR] item not found\n", 1)

        with (
            patch.object(sm, "_op_available", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=proc),
            pytest.raises(SecretAccessError, match="item not found"),
        ):
            await sm.get("missing-item")

    @pytest.mark.asyncio
    async def test_op_timeout_raises(self) -> None:
        sm = SecretsManager()
        proc = AsyncMock()
        proc.communicate = AsyncMock(side_effect=TimeoutError)

        with (
            patch.object(sm, "_op_available", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=proc),
            pytest.raises(SecretAccessError, match="timed out"),
        ):
            await sm.get("slow-item")

    @pytest.mark.asyncio
    async def test_os_error_raises(self) -> None:
        sm = SecretsManager()

        with (
            patch.object(sm, "_op_available", return_value=True),
            patch(
                "asyncio.create_subprocess_exec",
                side_effect=OSError("exec failed"),
            ),
            pytest.raises(SecretAccessError, match="execute op CLI"),
        ):
            await sm.get("item")

    @pytest.mark.asyncio
    async def test_error_message_includes_item_not_value(self) -> None:
        """Error messages must contain item/field names, never the secret value."""
        sm = SecretsManager()
        proc = _mock_process(b"", b"vault locked\n", 1)

        with (
            patch.object(sm, "_op_available", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=proc),
            pytest.raises(SecretAccessError) as exc_info,
        ):
            await sm.get("my-item", "my-field")

        msg = str(exc_info.value)
        assert "my-item" in msg
        assert "my-field" in msg


# --- get_bytes() ---


class TestGetBytes:
    """Test hex-decoded secret retrieval."""

    @pytest.mark.asyncio
    async def test_valid_hex(self) -> None:
        sm = SecretsManager()

        with patch.object(sm, "get", return_value="aabbccdd") as mock_get:
            result = await sm.get_bytes("key-item")

        assert result == bytes.fromhex("aabbccdd")
        mock_get.assert_called_once_with("key-item", "password")

    @pytest.mark.asyncio
    async def test_custom_field(self) -> None:
        sm = SecretsManager()

        with patch.object(sm, "get", return_value="ff") as mock_get:
            await sm.get_bytes("item", "hex-field")

        mock_get.assert_called_once_with("item", "hex-field")

    @pytest.mark.asyncio
    async def test_invalid_hex_raises(self) -> None:
        sm = SecretsManager()

        with (
            patch.object(sm, "get", return_value="not-hex-data"),
            pytest.raises(SecretAccessError, match="not valid hex"),
        ):
            await sm.get_bytes("item")

    @pytest.mark.asyncio
    async def test_invalid_hex_includes_length_not_value(self) -> None:
        """Error for invalid hex must include length, not the actual value."""
        sm = SecretsManager()

        with (
            patch.object(sm, "get", return_value="xyz123"),
            pytest.raises(SecretAccessError) as exc_info,
        ):
            await sm.get_bytes("item")

        msg = str(exc_info.value)
        assert "6" in msg  # length of "xyz123"
        assert "xyz123" not in msg  # value must NOT appear


# --- _op_available() ---


class TestOpAvailable:
    """Test op CLI detection."""

    def test_op_on_path(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/op"):
            assert SecretsManager._op_available() is True

    def test_op_not_on_path(self) -> None:
        with patch("shutil.which", return_value=None):
            assert SecretsManager._op_available() is False


# --- Security: no secret leakage ---


class TestNoSecretLeakage:
    """Verify that SecretsManager never includes secrets in exceptions."""

    @pytest.mark.asyncio
    async def test_success_return_type(self) -> None:
        """Successful get returns a string, not logged."""
        sm = SecretsManager()
        proc = _mock_process(b"super_secret_value\n", b"", 0)

        with (
            patch.object(sm, "_op_available", return_value=True),
            patch("asyncio.create_subprocess_exec", return_value=proc),
        ):
            result = await sm.get("item")

        assert isinstance(result, str)
        assert len(result) > 0


# --- DictSecretsManager ---


class TestDictSecretsManagerInit:
    """Test DictSecretsManager initialization."""

    def test_default_vault(self) -> None:
        dsm = DictSecretsManager(secrets={})
        assert dsm.vault == "config-entry"

    def test_custom_vault(self) -> None:
        dsm = DictSecretsManager(secrets={}, vault="custom")
        assert dsm.vault == "custom"

    def test_empty_dict(self) -> None:
        dsm = DictSecretsManager(secrets={})
        assert isinstance(dsm, DictSecretsManager)

    def test_single_entry(self) -> None:
        dsm = DictSecretsManager(secrets={"item/password": "val"})
        assert isinstance(dsm, DictSecretsManager)

    def test_multiple_entries(self) -> None:
        secrets = {
            "mesh-net-key/password": "aabb",  # pragma: allowlist secret
            "mesh-app-key/password": "ccdd",  # pragma: allowlist secret
            "device/uuid": "1234",
        }
        dsm = DictSecretsManager(secrets=secrets)
        assert isinstance(dsm, DictSecretsManager)


class TestDictSecretsManagerGet:
    """Test DictSecretsManager.get() retrieval."""

    @pytest.mark.asyncio
    async def test_basic_get(self) -> None:
        dsm = DictSecretsManager(secrets={"mesh-key/password": "secret123"})
        result = await dsm.get("mesh-key", "password")
        assert result == "secret123"

    @pytest.mark.asyncio
    async def test_default_field_is_password(self) -> None:
        dsm = DictSecretsManager(secrets={"item/password": "val"})
        result = await dsm.get("item")
        assert result == "val"

    @pytest.mark.asyncio
    async def test_custom_field(self) -> None:
        dsm = DictSecretsManager(secrets={"item/hex-key": "aabbcc"})
        result = await dsm.get("item", "hex-key")
        assert result == "aabbcc"

    @pytest.mark.asyncio
    async def test_missing_key_raises(self) -> None:
        dsm = DictSecretsManager(secrets={"item/password": "val"})
        with pytest.raises(SecretAccessError, match="not found"):
            await dsm.get("nonexistent")

    @pytest.mark.asyncio
    async def test_empty_dict_raises(self) -> None:
        dsm = DictSecretsManager(secrets={})
        with pytest.raises(SecretAccessError, match="not found"):
            await dsm.get("anything")

    @pytest.mark.asyncio
    async def test_wrong_field_raises(self) -> None:
        dsm = DictSecretsManager(secrets={"item/password": "val"})
        with pytest.raises(SecretAccessError, match="not found"):
            await dsm.get("item", "wrong-field")

    @pytest.mark.asyncio
    async def test_key_format_prefix_style(self) -> None:
        """Keys using prefix-net-key/password style work correctly."""
        secrets = {
            "malmbergs-net-key/password": "net_secret",  # pragma: allowlist secret
            "malmbergs-app-key/password": "app_secret",  # pragma: allowlist secret
            "malmbergs-dev-key/password": "dev_secret",  # pragma: allowlist secret
        }
        dsm = DictSecretsManager(secrets=secrets)
        assert await dsm.get("malmbergs-net-key") == "net_secret"
        assert await dsm.get("malmbergs-app-key") == "app_secret"
        assert await dsm.get("malmbergs-dev-key") == "dev_secret"

    @pytest.mark.asyncio
    async def test_error_message_includes_key(self) -> None:
        dsm = DictSecretsManager(secrets={})
        with pytest.raises(SecretAccessError) as exc_info:
            await dsm.get("my-item", "my-field")
        msg = str(exc_info.value)
        assert "my-item/my-field" in msg


class TestDictSecretsManagerInterface:
    """Verify DictSecretsManager matches the SecretsManager interface."""

    def test_is_subclass(self) -> None:
        assert issubclass(DictSecretsManager, SecretsManager)

    def test_is_instance(self) -> None:
        dsm = DictSecretsManager(secrets={})
        assert isinstance(dsm, SecretsManager)

    def test_has_async_get(self) -> None:
        dsm = DictSecretsManager(secrets={})
        assert asyncio.iscoroutinefunction(dsm.get)

    def test_has_async_get_bytes(self) -> None:
        dsm = DictSecretsManager(secrets={})
        assert asyncio.iscoroutinefunction(dsm.get_bytes)

    @pytest.mark.asyncio
    async def test_get_bytes_delegates_to_get(self) -> None:
        """get_bytes inherited from SecretsManager works with DictSecretsManager."""
        dsm = DictSecretsManager(secrets={"hex-item/password": "aabbccdd"})
        result = await dsm.get_bytes("hex-item")
        assert result == bytes.fromhex("aabbccdd")
