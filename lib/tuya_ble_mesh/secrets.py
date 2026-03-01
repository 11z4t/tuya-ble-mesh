"""1Password secrets manager for Malmbergs BT.

This is the ONLY module that accesses secrets (Rule S10).
All secret access goes through the ``SecretsManager`` class,
which reads values from 1Password via the ``op`` CLI.

SECURITY:
- Secret values are NEVER logged, printed, or included in exceptions.
- Only metadata (item names, field names, lengths) may appear in logs.
- If 1Password is unavailable, operations fail with ``SecretAccessError``.
  Do NOT fall back to environment variables or files.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

from tuya_ble_mesh.exceptions import SecretAccessError

_LOGGER = logging.getLogger(__name__)

_DEFAULT_VAULT = "malmbergs-bt"
_OP_TIMEOUT_SECONDS = 10


class SecretsManager:
    """Read secrets exclusively from 1Password.

    Uses ``op read`` to fetch secret values from the configured vault.
    The ``OP_SERVICE_ACCOUNT_TOKEN`` environment variable must be set
    for non-interactive access.

    Args:
        vault: 1Password vault name. Defaults to ``malmbergs-bt``.
    """

    def __init__(self, vault: str = _DEFAULT_VAULT) -> None:
        self._vault = vault

    @property
    def vault(self) -> str:
        """Return the vault name (not secret)."""
        return self._vault

    async def get(self, item: str, field: str = "password") -> str:
        """Read a secret value from 1Password.

        Calls ``op read "op://<vault>/<item>/<field>"``.

        Args:
            item: 1Password item name (e.g. ``mesh-key``).
            field: Field within the item. Defaults to ``password``.

        Returns:
            The secret value as a string (stripped of trailing newline).

        Raises:
            SecretAccessError: If ``op`` is not installed, the vault
                is unreachable, or the item/field does not exist.
        """
        ref = f"op://{self._vault}/{item}/{field}"

        if not self._op_available():
            msg = "1Password CLI (op) is not installed or not on PATH"
            raise SecretAccessError(msg)

        _LOGGER.debug("Reading secret: %s/%s [REDACTED]", item, field)

        try:
            process = await asyncio.create_subprocess_exec(
                "op",
                "read",
                ref,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=_OP_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            msg = f"1Password read timed out for {item}/{field}"
            raise SecretAccessError(msg) from None
        except OSError as exc:
            msg = f"Failed to execute op CLI: {exc}"
            raise SecretAccessError(msg) from exc

        if process.returncode != 0:
            error_text = stderr.decode("utf-8", errors="replace").strip()
            # Error messages from op may contain item/field names but
            # never the actual secret value, so they are safe to include.
            msg = f"op read failed for {item}/{field}: {error_text}"
            raise SecretAccessError(msg)

        value = stdout.decode("utf-8").rstrip("\n")
        _LOGGER.debug("Secret loaded: %s/%s (%d chars) [REDACTED]", item, field, len(value))
        return value

    async def get_bytes(self, item: str, field: str = "password") -> bytes:
        """Read a secret as raw bytes (hex-decoded).

        The secret value in 1Password must be stored as a hex string.

        Args:
            item: 1Password item name.
            field: Field within the item. Defaults to ``password``.

        Returns:
            The decoded bytes.

        Raises:
            SecretAccessError: If the value is not valid hex or vault
                access fails.
        """
        hex_value = await self.get(item, field)
        try:
            return bytes.fromhex(hex_value)
        except ValueError:
            msg = f"Secret {item}/{field} is not valid hex (length: {len(hex_value)})"
            raise SecretAccessError(msg) from None

    @staticmethod
    def _op_available() -> bool:
        """Check if the op CLI is on PATH."""
        return shutil.which("op") is not None
