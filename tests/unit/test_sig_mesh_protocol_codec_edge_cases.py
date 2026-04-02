"""Unit tests for sig_mesh_protocol_codec.py — uncovered edge cases.

Covers:
  config_appkey_add validation: 179-180, 182-183
  config_model_app_bind validation: 194-195, 197-198, 200-201
  parse_tuya_vendor_frame: 279-280 (short), 286-287 (timestamp), 290 (dp_data)
  tuya_vendor_timestamp_response: 298-307
"""

from __future__ import annotations

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

from tuya_ble_mesh.exceptions import ProtocolError
from tuya_ble_mesh.sig_mesh_protocol_codec import (
    TUYA_CMD_DP_DATA,
    TUYA_CMD_TIMESTAMP_SYNC,
    config_appkey_add,
    config_model_app_bind,
    parse_tuya_vendor_frame,
    tuya_vendor_timestamp_response,
)

# ── config_appkey_add validation ──────────────────────────────────────────────


class TestConfigAppkeyAddValidation:
    """Lines 179-180, 182-183: out-of-range net_idx / app_idx → ProtocolError."""

    def test_net_idx_negative_raises(self) -> None:
        with pytest.raises(ProtocolError, match="net_idx"):
            config_appkey_add(-1, 0, b"\x00" * 16)

    def test_net_idx_too_large_raises(self) -> None:
        with pytest.raises(ProtocolError, match="net_idx"):
            config_appkey_add(0x1000, 0, b"\x00" * 16)

    def test_app_idx_negative_raises(self) -> None:
        with pytest.raises(ProtocolError, match="app_idx"):
            config_appkey_add(0, -1, b"\x00" * 16)

    def test_app_idx_too_large_raises(self) -> None:
        with pytest.raises(ProtocolError, match="app_idx"):
            config_appkey_add(0, 0x1000, b"\x00" * 16)

    def test_valid_returns_bytes(self) -> None:
        result = config_appkey_add(0x001, 0x002, b"\xaa" * 16)
        assert isinstance(result, bytes)
        assert len(result) == 20  # 1 opcode byte + 3 idx bytes + 16 key bytes


# ── config_model_app_bind validation ─────────────────────────────────────────


class TestConfigModelAppBindValidation:
    """Lines 194-195, 197-198, 200-201: out-of-range args → ProtocolError."""

    def test_element_addr_too_large_raises(self) -> None:
        with pytest.raises(ProtocolError, match="element_addr"):
            config_model_app_bind(0x10000, 0, 0)

    def test_element_addr_negative_raises(self) -> None:
        with pytest.raises(ProtocolError, match="element_addr"):
            config_model_app_bind(-1, 0, 0)

    def test_app_idx_too_large_raises(self) -> None:
        with pytest.raises(ProtocolError, match="app_idx"):
            config_model_app_bind(0, 0x1000, 0)

    def test_app_idx_negative_raises(self) -> None:
        with pytest.raises(ProtocolError, match="app_idx"):
            config_model_app_bind(0, -1, 0)

    def test_model_id_too_large_raises(self) -> None:
        with pytest.raises(ProtocolError, match="model_id"):
            config_model_app_bind(0, 0, 0x10000)

    def test_model_id_negative_raises(self) -> None:
        with pytest.raises(ProtocolError, match="model_id"):
            config_model_app_bind(0, 0, -1)

    def test_valid_returns_bytes(self) -> None:
        result = config_model_app_bind(0x0001, 0x000, 0x1000)
        assert isinstance(result, bytes)


# ── parse_tuya_vendor_frame ───────────────────────────────────────────────────


class TestParseTuyaVendorFrame:
    """Lines 279-280, 286-287, 290: short params, timestamp sync, DP data."""

    def test_too_short_returns_empty_dps(self) -> None:
        """Lines 279-280: fewer than 2 bytes → returns frame with empty dps."""
        frame = parse_tuya_vendor_frame(b"\x01")
        assert frame.dps == []
        assert frame.command == 0

    def test_empty_params_returns_empty_dps(self) -> None:
        """Lines 279-280: empty bytes → returns frame with empty dps."""
        frame = parse_tuya_vendor_frame(b"")
        assert frame.dps == []

    def test_timestamp_sync_command(self) -> None:
        """Lines 286-287: TIMESTAMP_SYNC command → no DP parsing."""
        params = bytes([TUYA_CMD_TIMESTAMP_SYNC, 0x00])  # command=0x02, length=0
        frame = parse_tuya_vendor_frame(params)
        assert frame.command == TUYA_CMD_TIMESTAMP_SYNC
        assert frame.dps == []

    def test_dp_data_command(self) -> None:
        """Line 290: TUYA_CMD_DP_DATA → DP bytes parsed."""
        # Build a minimal DP: dp_id=1, type=0x01 (bool), length=1, value=0x01
        dp_bytes = bytes([0x01, 0x01, 0x00, 0x01, 0x01])
        params = bytes([TUYA_CMD_DP_DATA, len(dp_bytes)]) + dp_bytes
        frame = parse_tuya_vendor_frame(params)
        assert frame.command == TUYA_CMD_DP_DATA


# ── tuya_vendor_timestamp_response ───────────────────────────────────────────


class TestTuyaVendorTimestampResponse:
    """Lines 298-307: builds a well-formed timestamp response payload."""

    def test_returns_bytes(self) -> None:
        result = tuya_vendor_timestamp_response()
        assert isinstance(result, bytes)

    def test_length_is_correct(self) -> None:
        # 3 bytes opcode + 2 bytes frame header + 8 bytes (4 ts + 1 tz + 3 pad)
        result = tuya_vendor_timestamp_response()
        assert len(result) == 3 + 2 + 8

    def test_timestamp_sync_command_byte(self) -> None:
        result = tuya_vendor_timestamp_response()
        # First 3 bytes = opcode, 4th byte = TUYA_CMD_TIMESTAMP_SYNC (0x02)
        assert result[3] == TUYA_CMD_TIMESTAMP_SYNC

    def test_timestamp_value_is_recent(self) -> None:
        import time

        result = tuya_vendor_timestamp_response()
        ts = int.from_bytes(result[5:9], "big")
        assert abs(ts - int(time.time())) < 5  # Within 5 seconds
