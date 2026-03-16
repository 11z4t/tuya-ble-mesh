"""Unit tests for SIG Mesh protocol encoder/decoder."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.exceptions import MalformedPacketError, ProtocolError
from tuya_ble_mesh.sig_mesh_protocol import (
    TUYA_VENDOR_OPCODE,
    CompositionData,
    MeshKeys,
    ProxyPDU,
    SegmentHeader,
    config_appkey_add,
    config_composition_get,
    config_model_app_bind,
    decrypt_access_payload,
    decrypt_network_pdu,
    encrypt_network_pdu,
    format_status_response,
    generic_onoff_get,
    generic_onoff_set,
    make_access_segmented,
    make_access_unsegmented,
    make_proxy_pdu,
    parse_access_opcode,
    parse_composition_data,
    parse_proxy_pdu,
    parse_segment_header,
    parse_tuya_vendor_dps,
    reassemble_and_decrypt_segments,
)

# ============================================================
# Test fixture: well-known mesh keys from SIG Mesh spec
# ============================================================


# Mesh Profile 8.1.3 test vector
NET_KEY_HEX = "f7a2a44f8e8a8029064f173ddc1e2b00"  # pragma: allowlist secret
DEV_KEY_HEX = "00112233445566778899aabbccddeeff"  # pragma: allowlist secret
APP_KEY_HEX = "3216d1509884b533248541792b877f98"  # pragma: allowlist secret


@pytest.fixture()
def mesh_keys() -> MeshKeys:
    """Create MeshKeys from spec test vectors."""
    return MeshKeys(NET_KEY_HEX, DEV_KEY_HEX, APP_KEY_HEX)


# ============================================================
# MeshKeys
# ============================================================


class TestMeshKeys:
    """Test MeshKeys initialization and key derivation."""

    def test_nid_from_spec(self, mesh_keys: MeshKeys) -> None:
        """NID should match k2 spec sample data."""
        assert mesh_keys.nid == 0x7F

    def test_aid_from_spec(self, mesh_keys: MeshKeys) -> None:
        """AID should match k4 spec sample data."""
        assert mesh_keys.aid == 0x38

    def test_enc_key_from_spec(self, mesh_keys: MeshKeys) -> None:
        assert mesh_keys.enc_key == bytes.fromhex("9f589181a0f50de73c8070c7a6d27f46")

    def test_priv_key_from_spec(self, mesh_keys: MeshKeys) -> None:
        assert mesh_keys.priv_key == bytes.fromhex("4c715bd4a64b938f99b453351653124f")

    def test_network_id(self, mesh_keys: MeshKeys) -> None:
        assert mesh_keys.network_id == bytes.fromhex("ff046958233db014")

    def test_no_app_key(self) -> None:
        keys = MeshKeys(NET_KEY_HEX, DEV_KEY_HEX)
        assert keys.app_key is None
        assert keys.aid == 0

    def test_iv_index_stored(self) -> None:
        keys = MeshKeys(NET_KEY_HEX, DEV_KEY_HEX, iv_index=42)
        assert keys.iv_index == 42


# ============================================================
# Network PDU
# ============================================================


class TestNetworkPDU:
    """Test network PDU encrypt/decrypt roundtrip."""

    def test_encrypt_decrypt_roundtrip(self, mesh_keys: MeshKeys) -> None:
        transport_pdu = b"\x00\x56\x34\x12\x63\x96\x47\x71\x73\x4f\xbd\x76\xe3\xb4\x05\x19"
        pdu = encrypt_network_pdu(
            mesh_keys.enc_key,
            mesh_keys.priv_key,
            mesh_keys.nid,
            ctl=0,
            ttl=4,
            seq=100,
            src=0x0001,
            dst=0x00AA,
            transport_pdu=transport_pdu,
        )
        result = decrypt_network_pdu(
            mesh_keys.enc_key,
            mesh_keys.priv_key,
            mesh_keys.nid,
            pdu,
        )
        assert result is not None
        assert result.ctl == 0
        assert result.ttl == 4
        assert result.seq == 100
        assert result.src == 0x0001
        assert result.dst == 0x00AA
        assert result.transport_pdu == transport_pdu

    def test_ctl_message_roundtrip(self, mesh_keys: MeshKeys) -> None:
        transport_pdu = b"\x01\x02\x03\x04"
        pdu = encrypt_network_pdu(
            mesh_keys.enc_key,
            mesh_keys.priv_key,
            mesh_keys.nid,
            ctl=1,
            ttl=7,
            seq=200,
            src=0x0001,
            dst=0x00AA,
            transport_pdu=transport_pdu,
        )
        result = decrypt_network_pdu(
            mesh_keys.enc_key,
            mesh_keys.priv_key,
            mesh_keys.nid,
            pdu,
        )
        assert result is not None
        assert result.ctl == 1
        assert result.ttl == 7

    def test_wrong_nid_returns_none(self, mesh_keys: MeshKeys) -> None:
        pdu = encrypt_network_pdu(
            mesh_keys.enc_key,
            mesh_keys.priv_key,
            mesh_keys.nid,
            ctl=0,
            ttl=4,
            seq=100,
            src=0x0001,
            dst=0x00AA,
            transport_pdu=b"\x01\x02\x03",
        )
        result = decrypt_network_pdu(
            mesh_keys.enc_key,
            mesh_keys.priv_key,
            0x00,  # Wrong NID
            pdu,
        )
        assert result is None

    def test_short_pdu_returns_none(self, mesh_keys: MeshKeys) -> None:
        result = decrypt_network_pdu(
            mesh_keys.enc_key,
            mesh_keys.priv_key,
            mesh_keys.nid,
            b"\x7f\x01\x02",
        )
        assert result is None


# ============================================================
# Transport Layer — Unsegmented
# ============================================================


class TestUnsegmentedAccess:
    """Test unsegmented access message creation."""

    def test_max_payload(self) -> None:
        key = b"\x42" * 16
        # 11 bytes is the max
        result = make_access_unsegmented(key, 0x0001, 0x00AA, 100, 0, b"\x01" * 11)
        # Header(1) + encrypted(11) + TransMIC(4) = 16
        assert len(result) == 16

    def test_too_large_raises(self) -> None:
        key = b"\x42" * 16
        with pytest.raises(ProtocolError, match="11 bytes"):
            make_access_unsegmented(key, 0x0001, 0x00AA, 100, 0, b"\x01" * 12)

    def test_header_byte_device_key(self) -> None:
        key = b"\x42" * 16
        result = make_access_unsegmented(key, 0x0001, 0x00AA, 100, 0, b"\x82\x01", akf=0, aid=0)
        # SEG=0, AKF=0, AID=0 → header = 0x00
        assert result[0] == 0x00

    def test_header_byte_app_key(self) -> None:
        key = b"\x42" * 16
        result = make_access_unsegmented(key, 0x0001, 0x00AA, 100, 0, b"\x82\x01", akf=1, aid=0x16)
        # SEG=0, AKF=1, AID=0x16 → header = 0x56
        assert result[0] == 0x56


# ============================================================
# Transport Layer — Segmented
# ============================================================


class TestSegmentedAccess:
    """Test segmented access message creation."""

    def test_20_byte_payload_yields_2_segments(self) -> None:
        key = b"\x42" * 16
        # 20-byte payload + 4-byte MIC = 24 → ceil(24/12) = 2 segments
        segments = make_access_segmented(key, 0x0001, 0x00AA, 500, 0, b"\x01" * 20)
        assert len(segments) == 2

    def test_sequence_numbers_increment(self) -> None:
        key = b"\x42" * 16
        segments = make_access_segmented(key, 0x0001, 0x00AA, 500, 0, b"\x01" * 20)
        assert segments[0][0] == 500
        assert segments[1][0] == 501

    def test_seg_bit_set(self) -> None:
        key = b"\x42" * 16
        segments = make_access_segmented(key, 0x0001, 0x00AA, 500, 0, b"\x01" * 20)
        for _, pdu in segments:
            assert pdu[0] & 0x80 == 0x80  # SEG=1

    def test_single_segment_for_12_bytes(self) -> None:
        key = b"\x42" * 16
        # 8-byte payload + 4-byte MIC = 12 → exactly 1 segment
        segments = make_access_segmented(key, 0x0001, 0x00AA, 500, 0, b"\x01" * 8)
        assert len(segments) == 1


# ============================================================
# Proxy PDU
# ============================================================


class TestProxyPDU:
    """Test Proxy PDU creation and parsing."""

    def test_make_proxy_pdu(self) -> None:
        network_pdu = b"\x7f\x01\x02\x03"
        result = make_proxy_pdu(network_pdu)
        assert result[0] == 0x00  # SAR=complete(0), type=network(0)
        assert result[1:] == network_pdu

    def test_parse_proxy_pdu(self) -> None:
        data = bytes([0x00]) + b"\x7f\x01\x02\x03"
        result = parse_proxy_pdu(data)
        assert isinstance(result, ProxyPDU)
        assert result.sar == 0
        assert result.pdu_type == 0
        assert result.payload == b"\x7f\x01\x02\x03"

    def test_parse_empty_raises(self) -> None:
        with pytest.raises(MalformedPacketError, match="Empty"):
            parse_proxy_pdu(b"")

    def test_roundtrip(self) -> None:
        network_pdu = b"\x7f\x01\x02\x03\x04\x05"
        proxy = make_proxy_pdu(network_pdu)
        parsed = parse_proxy_pdu(proxy)
        assert parsed.payload == network_pdu


# ============================================================
# Config Messages
# ============================================================


class TestConfigMessages:
    """Test config model message encoding."""

    def test_composition_get_default(self) -> None:
        result = config_composition_get()
        assert result == b"\x80\x08\x00"

    def test_composition_get_page_1(self) -> None:
        result = config_composition_get(page=1)
        assert result == b"\x80\x08\x01"

    def test_composition_get_invalid_page(self) -> None:
        with pytest.raises(ProtocolError, match="Page"):
            config_composition_get(page=256)

    def test_appkey_add_length(self) -> None:
        """AppKey Add should be 20 bytes (needs segmented transport)."""
        result = config_appkey_add(0, 0, b"\x42" * 16)
        assert len(result) == 20

    def test_appkey_add_opcode(self) -> None:
        result = config_appkey_add(0, 0, b"\x42" * 16)
        assert result[0] == 0x00  # Opcode 0x00

    def test_appkey_add_invalid_key_length(self) -> None:
        with pytest.raises(ProtocolError, match="16 bytes"):
            config_appkey_add(0, 0, b"\x42" * 8)

    def test_model_app_bind_length(self) -> None:
        """Model App Bind should be 8 bytes (fits unsegmented)."""
        result = config_model_app_bind(0x00AA, 0, 0x1000)
        assert len(result) == 8

    def test_model_app_bind_opcode(self) -> None:
        result = config_model_app_bind(0x00AA, 0, 0x1000)
        assert result[0] == 0x80
        assert result[1] == 0x3D

    def test_model_app_bind_content(self) -> None:
        result = config_model_app_bind(0x00AA, 0, 0x1000)
        # Opcode(2) + element(2 LE) + appidx(2 LE) + modelid(2 LE)
        assert result[2:4] == b"\xaa\x00"  # element 0x00AA LE
        assert result[4:6] == b"\x00\x00"  # app_idx 0 LE
        assert result[6:8] == b"\x00\x10"  # model_id 0x1000 LE


# ============================================================
# Generic OnOff Messages
# ============================================================


class TestGenericOnOff:
    """Test Generic OnOff model messages."""

    def test_onoff_set_on(self) -> None:
        result = generic_onoff_set(on=True, tid=1)
        assert result == b"\x82\x02\x01\x01"

    def test_onoff_set_off(self) -> None:
        result = generic_onoff_set(on=False, tid=2)
        assert result == b"\x82\x02\x00\x02"

    def test_onoff_get(self) -> None:
        result = generic_onoff_get()
        assert result == b"\x82\x01"


# ============================================================
# Access Layer Opcode Parsing
# ============================================================


class TestParseAccessOpcode:
    """Test access opcode parsing."""

    def test_1_byte_opcode(self) -> None:
        opcode, params = parse_access_opcode(b"\x00\x42\x43")
        assert opcode == 0x00
        assert params == b"\x42\x43"

    def test_2_byte_opcode(self) -> None:
        opcode, params = parse_access_opcode(b"\x80\x08\x00")
        assert opcode == 0x8008
        assert params == b"\x00"

    def test_3_byte_vendor_opcode(self) -> None:
        opcode, params = parse_access_opcode(b"\xcd\xd0\x07\x01\x02")
        assert opcode == 0xCDD007
        assert params == b"\x01\x02"

    def test_empty_raises(self) -> None:
        with pytest.raises(MalformedPacketError, match="Empty"):
            parse_access_opcode(b"")

    def test_truncated_2_byte_raises(self) -> None:
        with pytest.raises(MalformedPacketError, match="truncated"):
            parse_access_opcode(b"\x80")

    def test_truncated_3_byte_raises(self) -> None:
        with pytest.raises(MalformedPacketError, match="truncated"):
            parse_access_opcode(b"\xcd\xd0")


# ============================================================
# Status Response Formatting
# ============================================================


class TestFormatStatusResponse:
    """Test status response formatting."""

    def test_appkey_status_success(self) -> None:
        result = format_status_response(0x8003, b"\x00")
        assert "Success" in result

    def test_appkey_status_already_stored(self) -> None:
        result = format_status_response(0x8003, b"\x06")
        assert "KeyIndexAlreadyStored" in result

    def test_model_app_status_success(self) -> None:
        result = format_status_response(0x803E, b"\x00")
        assert "Success" in result

    def test_onoff_status_on(self) -> None:
        result = format_status_response(0x8204, b"\x01")
        assert "ON" in result

    def test_onoff_status_off(self) -> None:
        result = format_status_response(0x8204, b"\x00")
        assert "OFF" in result

    def test_onoff_status_with_transition(self) -> None:
        result = format_status_response(0x8204, b"\x00\x01\x05")
        assert "target=ON" in result
        assert "remaining=5" in result

    def test_composition_data(self) -> None:
        result = format_status_response(0x02, b"\x00" + b"\x42" * 10)
        assert "page=0" in result
        assert "10 bytes" in result

    def test_unknown_opcode(self) -> None:
        result = format_status_response(0x9999, b"\x01\x02")
        assert "0x9999" in result
        assert "2 bytes" in result


# ============================================================
# Decrypt Access Payload
# ============================================================


class TestDecryptAccessPayload:
    """Test access payload decryption."""

    def test_unsegmented_roundtrip(self, mesh_keys: MeshKeys) -> None:
        """Encrypt then decrypt an unsegmented message."""
        access_payload = generic_onoff_get()
        transport_pdu = make_access_unsegmented(
            mesh_keys.dev_key, 0x0001, 0x00AA, 100, 0, access_payload
        )
        result = decrypt_access_payload(mesh_keys, 0x0001, 0x00AA, 100, transport_pdu)
        assert result is not None
        assert not result.seg
        assert result.access_payload == access_payload

    def test_segmented_returns_raw(self, mesh_keys: MeshKeys) -> None:
        """Segmented messages should return seg=True, payload=None."""
        # Fake a segmented PDU header
        transport_pdu = bytes([0x80]) + b"\x00\x00\x00" + b"\x42" * 12
        result = decrypt_access_payload(mesh_keys, 0x00AA, 0x0001, 100, transport_pdu)
        assert result is not None
        assert result.seg is True
        assert result.access_payload is None

    def test_empty_pdu_returns_none(self, mesh_keys: MeshKeys) -> None:
        result = decrypt_access_payload(mesh_keys, 0x0001, 0x00AA, 100, b"")
        assert result is None

    def test_app_key_roundtrip(self, mesh_keys: MeshKeys) -> None:
        """Encrypt with app key, decrypt successfully."""
        access_payload = generic_onoff_set(on=True, tid=1)
        transport_pdu = make_access_unsegmented(
            mesh_keys.app_key,
            0x0001,
            0x00AA,
            200,
            0,
            access_payload,
            akf=1,
            aid=mesh_keys.aid,
        )
        result = decrypt_access_payload(mesh_keys, 0x0001, 0x00AA, 200, transport_pdu)
        assert result is not None
        assert result.akf == 1
        assert result.access_payload == access_payload


# ============================================================
# Segment Header Parsing
# ============================================================


class TestParseSegmentHeader:
    """Test parse_segment_header."""

    def test_valid_header(self) -> None:
        """Parse a well-formed segmented transport PDU."""
        # Build: SEG=1, AKF=0, AID=0 → hdr=0x80
        # SZMIC=0, SeqZero=500(0x1F4), SegO=0, SegN=1
        # info = (0<<23)|(500<<10)|(0<<5)|1 = 0x07D001
        hdr = 0x80
        info = (0 << 23) | (500 << 10) | (0 << 5) | 1
        import struct

        pdu = bytes([hdr]) + struct.pack(">I", info)[1:] + b"\xaa\xbb\xcc"
        result = parse_segment_header(pdu)
        assert isinstance(result, SegmentHeader)
        assert result.akf == 0
        assert result.aid == 0
        assert result.szmic == 0
        assert result.seq_zero == 500
        assert result.seg_o == 0
        assert result.seg_n == 1
        assert result.segment_data == b"\xaa\xbb\xcc"

    def test_akf_and_aid_parsed(self) -> None:
        """AKF=1, AID=0x38 should be correctly extracted."""
        import struct

        hdr = 0x80 | (1 << 6) | 0x38  # SEG=1, AKF=1, AID=0x38
        info = (0 << 23) | (100 << 10) | (0 << 5) | 0
        pdu = bytes([hdr]) + struct.pack(">I", info)[1:] + b"\x42"
        result = parse_segment_header(pdu)
        assert result.akf == 1
        assert result.aid == 0x38

    def test_szmic_set(self) -> None:
        """SZMIC=1 should be parsed correctly."""
        import struct

        hdr = 0x80
        info = (1 << 23) | (200 << 10) | (1 << 5) | 2
        pdu = bytes([hdr]) + struct.pack(">I", info)[1:] + b"\x42" * 12
        result = parse_segment_header(pdu)
        assert result.szmic == 1
        assert result.seg_o == 1
        assert result.seg_n == 2

    def test_too_short_raises(self) -> None:
        """PDU shorter than 4 bytes should raise MalformedPacketError."""
        with pytest.raises(MalformedPacketError, match="too short"):
            parse_segment_header(b"\x80\x00\x00")

    def test_not_segmented_raises(self) -> None:
        """PDU without SEG bit should raise MalformedPacketError."""
        with pytest.raises(MalformedPacketError, match="SEG bit"):
            parse_segment_header(b"\x00\x00\x00\x00")


# ============================================================
# Segmented Reassembly Roundtrip
# ============================================================


class TestReassembleAndDecrypt:
    """Test reassemble_and_decrypt_segments with make_access_segmented."""

    def test_roundtrip_dev_key(self, mesh_keys: MeshKeys) -> None:
        """Encrypt segmented, then reassemble and decrypt with dev key."""
        access_payload = b"\x80\x08\x00" + b"\x42" * 20  # 23 bytes
        segments = make_access_segmented(
            mesh_keys.dev_key,
            0x0001,
            0x00AA,
            1000,
            0,
            access_payload,
            akf=0,
            aid=0,
        )
        # Collect segment data
        seg_data: dict[int, bytes] = {}
        for _seq, transport_pdu in segments:
            hdr_result = parse_segment_header(transport_pdu)
            seg_data[hdr_result.seg_o] = hdr_result.segment_data

        seg_n = segments[0][1][3] & 0x1F  # from first segment info
        seq_zero = 1000 & 0x1FFF

        result = reassemble_and_decrypt_segments(
            mesh_keys,
            0x0001,
            0x00AA,
            seg_data,
            seg_n,
            0,
            seq_zero,
            akf=0,
        )
        assert result == access_payload

    def test_roundtrip_app_key(self, mesh_keys: MeshKeys) -> None:
        """Encrypt segmented with app key, then reassemble and decrypt."""
        access_payload = b"\x82\x02\x01\x01" + b"\xff" * 15  # 19 bytes
        segments = make_access_segmented(
            mesh_keys.app_key,
            0x0001,
            0x00AA,
            2000,
            0,
            access_payload,
            akf=1,
            aid=mesh_keys.aid,
        )
        seg_data: dict[int, bytes] = {}
        for _seq, transport_pdu in segments:
            hdr_result = parse_segment_header(transport_pdu)
            seg_data[hdr_result.seg_o] = hdr_result.segment_data

        seg_n = len(segments) - 1
        seq_zero = 2000 & 0x1FFF

        result = reassemble_and_decrypt_segments(
            mesh_keys,
            0x0001,
            0x00AA,
            seg_data,
            seg_n,
            0,
            seq_zero,
            akf=1,
        )
        assert result == access_payload

    def test_missing_segment_returns_none(self, mesh_keys: MeshKeys) -> None:
        """Missing segment should return None."""
        result = reassemble_and_decrypt_segments(
            mesh_keys,
            0x0001,
            0x00AA,
            {0: b"\x42" * 12},  # missing segment 1
            seg_n=1,
            szmic=0,
            seq_zero=100,
            akf=0,
        )
        assert result is None

    def test_wrong_key_returns_none(self, mesh_keys: MeshKeys) -> None:
        """Wrong key should fail decryption and return None."""
        access_payload = b"\x42" * 20
        segments = make_access_segmented(
            mesh_keys.dev_key,
            0x0001,
            0x00AA,
            500,
            0,
            access_payload,
        )
        seg_data: dict[int, bytes] = {}
        for _seq, transport_pdu in segments:
            hdr_result = parse_segment_header(transport_pdu)
            seg_data[hdr_result.seg_o] = hdr_result.segment_data

        # Use app key instead of dev key (akf=1 but encrypted with dev key)
        result = reassemble_and_decrypt_segments(
            mesh_keys,
            0x0001,
            0x00AA,
            seg_data,
            seg_n=len(segments) - 1,
            szmic=0,
            seq_zero=500 & 0x1FFF,
            akf=1,
        )
        assert result is None

    def test_no_key_returns_none(self) -> None:
        """No app key when akf=1 should return None."""
        keys = MeshKeys(NET_KEY_HEX, DEV_KEY_HEX)  # No app key
        result = reassemble_and_decrypt_segments(
            keys,
            0x0001,
            0x00AA,
            {0: b"\x42" * 12},
            seg_n=0,
            szmic=0,
            seq_zero=100,
            akf=1,
        )
        assert result is None


# ============================================================
# Tuya Vendor DP Parsing
# ============================================================


class TestParseTuyaVendorDPs:
    """Test parse_tuya_vendor_dps."""

    def test_single_dp(self) -> None:
        # dp_id=1, dp_type=1, dp_len=1, value=0x01
        data = bytes([1, 1, 1, 0x01])
        result = parse_tuya_vendor_dps(data)
        assert len(result) == 1
        assert result[0].dp_id == 1
        assert result[0].dp_type == 1
        assert result[0].value == b"\x01"

    def test_multiple_dps(self) -> None:
        # Two DPs: dp1(id=18,type=2,len=4,val=0x00000064) + dp2(id=17,type=2,len=4,val=0x000003E8)
        data = bytes([18, 2, 4, 0, 0, 0, 100, 17, 2, 4, 0, 0, 3, 0xE8])
        result = parse_tuya_vendor_dps(data)
        assert len(result) == 2
        assert result[0].dp_id == 18
        assert int.from_bytes(result[0].value, "big") == 100
        assert result[1].dp_id == 17
        assert int.from_bytes(result[1].value, "big") == 1000

    def test_empty_params(self) -> None:
        result = parse_tuya_vendor_dps(b"")
        assert result == []

    def test_truncated_header(self) -> None:
        result = parse_tuya_vendor_dps(b"\x01\x02")
        assert result == []

    def test_truncated_value(self) -> None:
        # dp_id=1, dp_type=1, dp_len=4, but only 2 bytes of value
        data = bytes([1, 1, 4, 0xAA, 0xBB])
        result = parse_tuya_vendor_dps(data)
        assert result == []

    def test_zero_length_value(self) -> None:
        data = bytes([1, 1, 0])
        result = parse_tuya_vendor_dps(data)
        assert len(result) == 1
        assert result[0].value == b""

    def test_vendor_opcode_constant(self) -> None:
        assert TUYA_VENDOR_OPCODE == 0xCDD007


# ============================================================
# Composition Data Parsing
# ============================================================


class TestParseCompositionData:
    """Test parse_composition_data."""

    def test_valid_composition(self) -> None:
        import struct

        # page=0, CID=0x07D0, PID=0x0001, VID=0x0002, CRPL=10, Features=0x0003
        page = b"\x00"
        header = struct.pack("<HHHHH", 0x07D0, 0x0001, 0x0002, 10, 0x0003)
        elements = b"\x42\x43\x44"
        data = page + header + elements

        result = parse_composition_data(data)
        assert isinstance(result, CompositionData)
        assert result.cid == 0x07D0
        assert result.pid == 0x0001
        assert result.vid == 0x0002
        assert result.crpl == 10
        assert result.features == 0x0003
        assert result.raw_elements == elements

    def test_too_short_raises(self) -> None:
        with pytest.raises(MalformedPacketError, match="too short"):
            parse_composition_data(b"\x00\x01\x02\x03")

    def test_minimal_valid(self) -> None:
        import struct

        page = b"\x00"
        header = struct.pack("<HHHHH", 0, 0, 0, 0, 0)
        data = page + header
        result = parse_composition_data(data)
        assert result.cid == 0
        assert result.raw_elements == b""
