"""Miscellaneous edge case tests covering small uncovered paths.

Covers:
  device.py: 388-389 (safety-net MeshConnectionError, max_retries=0)
  dps.py: 190 (load_profile_by_model with empty model)
  power.py: 43-44 (BridgePowerController timeout <= 0 validation)
  scanner.py: 125-128 (RSSI update in scan callback), 197-198 (empty MAC)
  secrets.py: 68-69 (empty item/field validation)
  sig_mesh_crypto.py: 266-267 (mic_len invalid)
  sig_mesh_device.py: 393-394 (TypeError during key zeroing)
  sig_mesh_device_commands.py: 88, 91 (abstract NotImplementedError)
  sig_mesh_protocol.py: 239-241 (CryptoError in network decrypt)
  sig_mesh_protocol.py: 373-374 (key is None in upper transport)
  sig_mesh_protocol.py: 379-381 (CryptoError in upper transport decrypt)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

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

from tuya_ble_mesh.connection import ConnectionState
from tuya_ble_mesh.device import MeshDevice
from tuya_ble_mesh.dps import load_profile_by_model
from tuya_ble_mesh.exceptions import (
    CryptoError,
    MeshConnectionError,
    PowerControlError,
    ProtocolError,
    SecretAccessError,
)
from tuya_ble_mesh.power import BridgePowerController
from tuya_ble_mesh.scanner import mac_to_bytes
from tuya_ble_mesh.secrets import SecretsManager
from tuya_ble_mesh.sig_mesh_crypto import mesh_aes_ccm_decrypt
from tuya_ble_mesh.sig_mesh_device_commands import SIGMeshDeviceCommandsMixin
from tuya_ble_mesh.sig_mesh_protocol import (
    MeshKeys,
    decrypt_access_payload,
    decrypt_network_pdu,
)

MAC = "DC:23:4D:21:43:A5"
SESSION_KEY = b"\x00" * 16


# ── device.py lines 388-389 ───────────────────────────────────────────────────


class TestSendNowSafetyNet:
    """Lines 388-389: safety-net MeshConnectionError when max_retries=0."""

    @pytest.mark.asyncio
    async def test_zero_max_retries_raises_mesh_connection_error(self) -> None:
        """Lines 388-389: loop is empty (max_retries=0) and last_error is None."""
        device = MeshDevice(MAC, b"out_of_mesh", b"123456")
        conn = device._conn
        conn._state = ConnectionState.READY
        conn._session_key = bytearray(SESSION_KEY)
        client = AsyncMock()
        client.write_gatt_char = AsyncMock()
        conn._client = client

        with (
            patch("tuya_ble_mesh.device.encode_command_packet", return_value=b"\x00" * 16),
            patch.object(conn, "next_sequence", new_callable=AsyncMock, return_value=1),
            pytest.raises(MeshConnectionError, match="failed after 0 attempts"),
        ):
            await device._send_now(0x01, b"", 0x00B0, max_retries=0)


# ── dps.py line 190 ───────────────────────────────────────────────────────────


class TestLoadProfileByModelEmpty:
    """Line 190: load_profile_by_model returns None for empty model string."""

    def test_empty_model_returns_none(self) -> None:
        assert load_profile_by_model("") is None

    def test_none_model_returns_none(self) -> None:
        assert load_profile_by_model(None) is None  # type: ignore[arg-type]


# ── power.py lines 43-44 ──────────────────────────────────────────────────────


class TestBridgePowerControllerInit:
    """Lines 43-44: timeout <= 0 raises PowerControlError."""

    def test_zero_timeout_raises(self) -> None:
        with pytest.raises(PowerControlError, match="Timeout must be positive"):
            BridgePowerController("192.168.1.50", timeout=0)

    def test_negative_timeout_raises(self) -> None:
        with pytest.raises(PowerControlError, match="Timeout must be positive"):
            BridgePowerController("192.168.1.50", timeout=-1.0)


# ── scanner.py lines 125-128 ──────────────────────────────────────────────────


class TestScanCallback:
    """Lines 125-128: callback updates map when RSSI improves."""

    @pytest.mark.asyncio
    async def test_callback_updates_map_with_better_rssi(self) -> None:
        """Lines 125-128: device seen twice — stronger RSSI replaces weaker."""
        from tuya_ble_mesh.scanner import scan_for_devices

        def _make_ble_device(address: str, rssi: int) -> tuple[MagicMock, MagicMock]:
            device = MagicMock()
            device.address = address
            device.name = "out_of_mesh"
            adv = MagicMock()
            adv.rssi = rssi
            adv.service_uuids = []
            adv.manufacturer_data = {}
            return device, adv

        class FakeScanner:
            """Fires two detection events in __aenter__ before the sleep."""

            def __init__(self, detection_callback: object = None, **_kw: object) -> None:
                self._cb = detection_callback

            async def __aenter__(self) -> FakeScanner:
                if callable(self._cb):
                    # First advertisement at -80 dBm
                    d1, a1 = _make_ble_device("AA:BB:CC:DD:EE:FF", -80)
                    self._cb(d1, a1)
                    # Second advertisement at -60 dBm (stronger → replaces)
                    d2, a2 = _make_ble_device("AA:BB:CC:DD:EE:FF", -60)
                    self._cb(d2, a2)
                return self

            async def __aexit__(self, *_: object) -> None:
                pass

        with (
            patch("tuya_ble_mesh.scanner.BleakScanner", FakeScanner),
            patch(
                "tuya_ble_mesh.scanner.asyncio.sleep",
                new_callable=AsyncMock,
            ),
        ):
            result = await scan_for_devices(timeout=0.001)

        assert len(result) == 1
        assert result[0].rssi == -60


# ── scanner.py lines 197-198 ──────────────────────────────────────────────────


class TestMacToBytesEmpty:
    """Lines 197-198: empty MAC raises ProtocolError."""

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ProtocolError, match="cannot be empty"):
            mac_to_bytes("")

    def test_none_raises(self) -> None:
        with pytest.raises(ProtocolError, match="cannot be empty"):
            mac_to_bytes(None)  # type: ignore[arg-type]


# ── secrets.py lines 68-69 ────────────────────────────────────────────────────


class TestOnePasswordSecretsEmpty:
    """Lines 68-69: empty item or field raises SecretAccessError."""

    @pytest.mark.asyncio
    async def test_empty_item_raises(self) -> None:
        mgr = SecretsManager("myvault")
        with pytest.raises(SecretAccessError, match="Item and field cannot be empty"):
            await mgr.get("", "password")

    @pytest.mark.asyncio
    async def test_empty_field_raises(self) -> None:
        mgr = SecretsManager("myvault")
        with pytest.raises(SecretAccessError, match="Item and field cannot be empty"):
            await mgr.get("myitem", "")


# ── sig_mesh_crypto.py lines 266-267 ─────────────────────────────────────────


class TestMeshAesCcmDecryptMicLen:
    """Lines 266-267: invalid mic_len raises CryptoError."""

    def test_mic_len_6_raises(self) -> None:
        key = b"\x00" * 16
        nonce = b"\x00" * 13
        ct = b"\x00" * 20
        with pytest.raises(CryptoError, match="mic_len must be 4 or 8"):
            mesh_aes_ccm_decrypt(key, nonce, ct, mic_len=6)

    def test_mic_len_0_raises(self) -> None:
        key = b"\x00" * 16
        nonce = b"\x00" * 13
        ct = b"\x00" * 20
        with pytest.raises(CryptoError, match="mic_len must be 4 or 8"):
            mesh_aes_ccm_decrypt(key, nonce, ct, mic_len=0)


# ── sig_mesh_device.py lines 393-394 ─────────────────────────────────────────


class TestSIGMeshDeviceKeyZeroingTypeError:
    """Lines 393-394: TypeError during key zeroing is swallowed."""

    @pytest.mark.asyncio
    async def test_type_error_during_key_zeroing_swallowed(self) -> None:
        """Lines 393-394: bytearray subclass that raises TypeError on setitem."""
        from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice

        class FrozenBytearray(bytearray):
            def __setitem__(self, key: object, value: object) -> None:
                raise TypeError("frozen")

        dev = SIGMeshDevice(
            "DC:23:4D:21:43:A5",
            0x00AA,
            0x0001,
            MagicMock(),
        )

        fake_keys = MagicMock()
        fake_keys.net_key = FrozenBytearray(16)  # isinstance(val, bytearray) = True
        dev._keys = fake_keys
        dev._client = None
        dev._pending_notify_tasks = set()

        # Must not raise
        await dev.disconnect()
        assert dev._keys is None


# ── sig_mesh_device_commands.py lines 88, 91 ─────────────────────────────────


class TestSIGMeshDeviceCommandsAbstract:
    """Lines 88, 91: abstract _next_seq/_next_seqs raise NotImplementedError."""

    @pytest.mark.asyncio
    async def test_next_seq_raises_not_implemented(self) -> None:
        """Line 88: base _next_seq raises NotImplementedError."""

        class ConcreteCommands(SIGMeshDeviceCommandsMixin):
            pass

        obj = ConcreteCommands.__new__(ConcreteCommands)
        with pytest.raises(NotImplementedError):
            await obj._next_seq()

    @pytest.mark.asyncio
    async def test_next_seqs_raises_not_implemented(self) -> None:
        """Line 91: base _next_seqs raises NotImplementedError."""

        class ConcreteCommands(SIGMeshDeviceCommandsMixin):
            pass

        obj = ConcreteCommands.__new__(ConcreteCommands)
        with pytest.raises(NotImplementedError):
            await obj._next_seqs(2)


# ── sig_mesh_protocol.py lines 239-241 ───────────────────────────────────────


class TestDecryptNetworkPduCryptoError:
    """Lines 239-241: CryptoError during decrypt_network_pdu → returns None."""

    def test_crypto_error_returns_none(self) -> None:
        """Lines 239-241: mesh_aes_ccm_decrypt raises CryptoError → None."""
        # Build a PDU that passes all checks up to decryption.
        # nid=0x21, pdu must be >= 14 bytes so privacy_random is 7 bytes
        # and pecb_input is exactly 16 bytes for aes_ecb.
        nid = 0x21
        pdu = bytes([nid]) + b"\x00" * 13  # 14 bytes, passes >= 10 and pecb check

        enc_key = b"\x00" * 16
        priv_key = b"\x00" * 16

        with patch(
            "tuya_ble_mesh.sig_mesh_protocol.mesh_aes_ccm_decrypt",
            side_effect=CryptoError("bad tag"),
        ):
            result = decrypt_network_pdu(enc_key, priv_key, nid, pdu)

        assert result is None


# ── sig_mesh_protocol.py lines 373-374 ───────────────────────────────────────


class TestDecryptAccessPayloadKeyNone:
    """Lines 373-374: key is None → return None (unsegmented, akf=1, app_key absent)."""

    def test_app_key_none_returns_none(self) -> None:
        """Lines 373-374: keys.app_key is None for akf=1 unsegmented → None."""
        keys = MeshKeys(
            net_key_hex="f7a2a44f8e8a8029064f173ddc1e2b00",  # pragma: allowlist secret
            dev_key_hex="00112233445566778899aabbccddeeff",  # pragma: allowlist secret
            app_key_hex=None,  # No app key → line 373-374 hit for akf=1
        )
        # Unsegmented (seg=False): first byte bit7=0 → hdr & MESH_SEG_BIT == 0
        # akf=1 (bit6): hdr = 0b0100_0000 = 0x40
        hdr = 0x40  # seg=0, akf=1, aid=0
        transport_pdu = bytes([hdr]) + b"\x00" * 10

        result = decrypt_access_payload(
            keys, src=0x0001, dst=0x00B0, seq=1, transport_pdu=transport_pdu
        )

        assert result is None


# ── sig_mesh_protocol.py lines 379-381 ───────────────────────────────────────


class TestDecryptAccessPayloadCryptoError:
    """Lines 379-381: CryptoError during upper transport decrypt → None."""

    def test_crypto_error_returns_none(self) -> None:
        """Lines 379-381: mesh_aes_ccm_decrypt raises CryptoError → None."""
        keys = MeshKeys(
            net_key_hex="f7a2a44f8e8a8029064f173ddc1e2b00",  # pragma: allowlist secret
            dev_key_hex="00112233445566778899aabbccddeeff",  # pragma: allowlist secret
            app_key_hex="3216d1509884b533248541792b877f98",  # pragma: allowlist secret
        )
        # Unsegmented (seg=0), akf=1: hdr = 0x40
        hdr = 0x40
        transport_pdu = bytes([hdr]) + b"\x00" * 10

        with patch(
            "tuya_ble_mesh.sig_mesh_protocol.mesh_aes_ccm_decrypt",
            side_effect=CryptoError("bad tag"),
        ):
            result = decrypt_access_payload(
                keys, src=0x0001, dst=0x00B0, seq=1, transport_pdu=transport_pdu
            )

        assert result is None
