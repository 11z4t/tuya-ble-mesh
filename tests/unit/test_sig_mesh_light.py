"""Unit tests for SIG Mesh light support.

Covers:
- Lightness / CTL / HSL codec builders and status parsers
- Full Composition Data Page 0 element parsing (Tuya RGBCW layout)
- SeqAuth derivation and segmented reassembly with SEQ > 8191
- Segment Acknowledgment PDU format
- SIGMeshLight command encoding and status handling (real crypto round trip)
- configure_sig_node post-provisioning flow
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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

from tuya_ble_mesh.exceptions import (
    DisconnectedError,
    MalformedPacketError,
    MeshConnectionError,
    ProtocolError,
    SIGMeshError,
)
from tuya_ble_mesh.secrets import DictSecretsManager
from tuya_ble_mesh.sig_mesh_light import (
    MODE_COLOR,
    MODE_WHITE,
    SIGLightState,
    SIGMeshLight,
    configure_sig_node,
    model_elements_from_composition,
)
from tuya_ble_mesh.sig_mesh_protocol import (
    MeshKeys,
    decrypt_access_payload,
    decrypt_network_pdu,
    encrypt_network_pdu,
    make_access_segmented,
    make_access_unsegmented,
    make_proxy_pdu,
    parse_access_opcode,
    parse_proxy_pdu,
    parse_segment_header,
    reassemble_and_decrypt_segments,
    seq_auth_from_seq,
)
from tuya_ble_mesh.sig_mesh_protocol_codec import (
    OP_CONFIG_COMPOSITION_STATUS,
    OP_LIGHT_CTL_SET,
    OP_LIGHT_CTL_STATUS,
    OP_LIGHT_HSL_SET,
    OP_LIGHT_HSL_STATUS,
    OP_LIGHT_LIGHTNESS_SET,
    config_model_app_bind_vendor,
    config_model_pub_set,
    light_ctl_set,
    light_hsl_set,
    light_lightness_set,
    parse_composition_data,
    parse_ctl_status,
    parse_ctl_temp_range_status,
    parse_hsl_status,
    parse_lightness_status,
    segment_ack,
)

_NET = "7dd7364cd842ad18c17c2b820c84c3d6"  # pragma: allowlist secret
_DEV = "9d6dd0e96eb25dc19a40ed9914f8f03f"  # pragma: allowlist secret
_APP = "63964771734fbd76e3b40519d1d94a48"  # pragma: allowlist secret
_MAC = "DC:23:53:DA:B3:D4"
_NODE = 0x00B0
_US = 0x0001


@pytest.fixture(autouse=True)
def _fast_status_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sets wait for a Status the test never sends; keep that wait short."""
    monkeypatch.setattr("tuya_ble_mesh.sig_mesh_light._SET_STATUS_TIMEOUT", 0.01)


def _composition_params() -> bytes:
    """Composition Data Status params for a Tuya-style 6-element RGBCW bulb."""
    header = struct.pack("<HHHHH", 0x07D0, 0x0001, 0x0002, 0x0010, 0x0003)

    def element(sig: list[int], vendor: list[tuple[int, int]]) -> bytes:
        out = struct.pack("<HBB", 0x0000, len(sig), len(vendor))
        out += b"".join(struct.pack("<H", m) for m in sig)
        out += b"".join(struct.pack("<HH", c, m) for c, m in vendor)
        return out

    elements = (
        element(
            [0x0000, 0x0002, 0x1000, 0x1300, 0x1301, 0x1303, 0x1304, 0x1307, 0x1308],
            [(0x07D0, 0x0004)],
        )
        + element([0x1002, 0x1306], [])
        + element([0x1002, 0x130A], [])
        + element([0x1002, 0x130B], [])
        + element([0x1000], [])
        + element([0x1000], [])
    )
    return b"\x00" + header + elements


def _keys() -> MeshKeys:
    return MeshKeys(_NET, _DEV, _APP, iv_index=0)


def _light(**kwargs: object) -> SIGMeshLight:
    secrets = DictSecretsManager(
        {
            "cfg-net-key/password": _NET,
            f"cfg-dev-key-{_NODE:04x}/password": _DEV,
            "cfg-app-key/password": _APP,
        }
    )
    light = SIGMeshLight(_MAC, _NODE, _US, secrets, op_item_prefix="cfg", **kwargs)  # type: ignore[arg-type]
    light._keys = _keys()
    client = MagicMock()
    client.write_gatt_char = AsyncMock()
    client.is_connected = True
    light._client = client
    return light


def _sent_access(light: SIGMeshLight, call_index: int = -1) -> tuple[int, int, bytes]:
    """Decrypt a PDU written by the light; return (dst, opcode, params)."""
    keys = _keys()
    data = light._client.write_gatt_char.await_args_list[call_index].args[1]
    net = decrypt_network_pdu(
        keys.enc_key, keys.priv_key, keys.nid, parse_proxy_pdu(data).payload, iv_index=0
    )
    assert net is not None
    msg = decrypt_access_payload(keys, net.src, net.dst, net.seq, net.transport_pdu)
    assert msg is not None and msg.access_payload is not None
    opcode, params = parse_access_opcode(msg.access_payload)
    return net.dst, opcode, params


def _device_status(access_payload: bytes, seq: int = 500) -> bytes:
    """Build a proxy PDU as sent by the bulb (app key) to us."""
    keys = _keys()
    assert keys.app_key is not None
    transport = make_access_unsegmented(
        keys.app_key, _NODE, _US, seq, 0, access_payload, akf=1, aid=keys.aid
    )
    net = encrypt_network_pdu(
        keys.enc_key,
        keys.priv_key,
        keys.nid,
        ctl=0,
        ttl=5,
        seq=seq,
        src=_NODE,
        dst=_US,
        transport_pdu=transport,
    )
    return make_proxy_pdu(net)


# ============================================================
# Codec
# ============================================================


class TestLightCodec:
    def test_lightness_set_layout(self) -> None:
        payload = light_lightness_set(0x1234, 7)
        assert payload == b"\x82\x4c\x34\x12\x07"
        assert light_lightness_set(1, 0, ack=False)[:2] == b"\x82\x4d"

    def test_ctl_set_layout(self) -> None:
        payload = light_ctl_set(0xFFFF, 4000, 3)
        assert payload[:2] == b"\x82\x5e"
        assert struct.unpack("<HHhB", payload[2:]) == (0xFFFF, 4000, 0, 3)

    def test_ctl_set_rejects_out_of_range_temp(self) -> None:
        with pytest.raises(ProtocolError):
            light_ctl_set(100, 500, 0)

    def test_hsl_set_layout_is_lightness_hue_saturation(self) -> None:
        payload = light_hsl_set(0x8000, 0x5555, 0xFFFF, 9)
        assert payload[:2] == b"\x82\x76"
        assert struct.unpack("<HHHB", payload[2:]) == (0x8000, 0x5555, 0xFFFF, 9)

    def test_hsl_set_rejects_out_of_range(self) -> None:
        with pytest.raises(ProtocolError):
            light_hsl_set(0x10000, 0, 0, 0)

    def test_status_parsers(self) -> None:
        assert parse_lightness_status(b"\x00\x80").lightness == 0x8000
        ctl = parse_ctl_status(struct.pack("<HHHHB", 0x4000, 3000, 0x4000, 3000, 0))
        assert (ctl.lightness, ctl.temperature_k) == (0x4000, 3000)
        hsl = parse_hsl_status(struct.pack("<HHH", 1, 2, 3))
        assert (hsl.lightness, hsl.hue, hsl.saturation) == (1, 2, 3)
        rng = parse_ctl_temp_range_status(b"\x00" + struct.pack("<HH", 2700, 6500))
        assert (rng.status, rng.min_k, rng.max_k) == (0, 2700, 6500)

    @pytest.mark.parametrize("parser", [parse_lightness_status, parse_ctl_status, parse_hsl_status])
    def test_status_parsers_reject_short(self, parser: object) -> None:
        with pytest.raises(MalformedPacketError):
            parser(b"\x01")  # type: ignore[operator]

    def test_vendor_bind_layout(self) -> None:
        payload = config_model_app_bind_vendor(0x00B0, 0, 0x07D0, 0x0004)
        assert payload == b"\x80\x3d" + struct.pack("<HHHH", 0x00B0, 0, 0x07D0, 0x0004)

    def test_pub_set_layout(self) -> None:
        payload = config_model_pub_set(0x00B0, 0x0001, 0, 0x1307)
        assert payload[0] == 0x03
        assert len(payload) == 12  # needs segmented transport
        assert struct.unpack("<HHH", payload[1:7]) == (0x00B0, 0x0001, 0)
        assert struct.unpack("<H", payload[10:])[0] == 0x1307

    def test_segment_ack_layout(self) -> None:
        pdu = segment_ack(0x1ABC, 0b111)
        assert pdu[0] == 0x00
        info = struct.unpack(">H", pdu[1:3])[0]
        assert (info >> 2) & 0x1FFF == 0x1ABC and not info >> 15
        assert struct.unpack(">I", pdu[3:])[0] == 0b111


class TestCompositionElements:
    def test_parses_six_element_tuya_rgbcw(self) -> None:
        comp = parse_composition_data(_composition_params())
        assert comp.cid == 0x07D0
        assert len(comp.elements) == 6
        assert 0x1307 in comp.elements[0].sig_models
        assert comp.elements[0].vendor_models == ((0x07D0, 0x0004),)
        assert comp.find_model(0x1306) == 1
        assert comp.find_model(0x130B) == 3
        assert comp.find_model(0x9999) is None

    def test_model_elements_map(self) -> None:
        comp = parse_composition_data(_composition_params())
        mapping = model_elements_from_composition(comp)
        assert mapping[0x1303] == 0 and mapping[0x130A] == 2 and mapping[0x1002] == 1

    def test_truncated_element_list_keeps_complete_elements(self) -> None:
        comp = parse_composition_data(_composition_params()[:-3])
        assert len(comp.elements) == 5


class TestSeqAuth:
    @pytest.mark.parametrize("seq_start", [5, 8191, 8192, 20000, 0xFFFF00])
    def test_seq_auth_recovered_from_any_segment(self, seq_start: int) -> None:
        for offset in range(4):
            assert seq_auth_from_seq(seq_start + offset, seq_start & 0x1FFF) == seq_start

    def test_reassembly_with_high_seq_needs_seq_auth(self) -> None:
        keys = _keys()
        payload = b"\x02\x00" + b"\x5a" * 40
        segs = make_access_segmented(keys.dev_key, _NODE, _US, 20000, 0, payload)
        data = {
            parse_segment_header(p).seg_o: parse_segment_header(p).segment_data for _, p in segs
        }
        seg_n = len(segs) - 1
        seq_zero = 20000 & 0x1FFF
        # Legacy SeqZero nonce fails once SEQ > 8191
        assert (
            reassemble_and_decrypt_segments(keys, _NODE, _US, data, seg_n, 0, seq_zero, 0) is None
        )
        assert (
            reassemble_and_decrypt_segments(
                keys, _NODE, _US, data, seg_n, 0, seq_zero, 0, seq_auth=20000
            )
            == payload
        )


# ============================================================
# SIGMeshLight
# ============================================================


class TestSIGMeshLightCommands:
    @pytest.mark.asyncio
    async def test_send_ctl_encodes_and_clamps(self) -> None:
        light = _light(temp_range_k=(2700, 6500))
        await light.send_ctl(0x8000, 10000)
        dst, opcode, params = _sent_access(light, 0)
        assert (dst, opcode) == (_NODE, OP_LIGHT_CTL_SET)
        assert struct.unpack_from("<HH", params) == (0x8000, 6500)
        # Tuya applies colour temperature only from CTL Temperature Set (0x8264)
        dst, opcode, params = _sent_access(light, 1)
        assert (dst, opcode) == (_NODE, 0x8264)
        assert struct.unpack_from("<Hh", params) == (6500, 0)
        assert light.light_state.mode == MODE_WHITE

    @pytest.mark.asyncio
    async def test_send_hsl_targets_hsl_element(self) -> None:
        light = _light(model_elements={0x1307: 0})
        await light.send_hsl(0x8000, 0x1000, 0xFFFF)
        dst, opcode, params = _sent_access(light)
        assert (dst, opcode) == (_NODE, OP_LIGHT_HSL_SET)
        assert struct.unpack_from("<HHH", params) == (0x8000, 0x1000, 0xFFFF)
        assert light.light_state.mode == MODE_COLOR

    @pytest.mark.asyncio
    async def test_send_lightness(self) -> None:
        light = _light()
        await light.send_lightness(70000)
        _dst, opcode, params = _sent_access(light)
        assert opcode == OP_LIGHT_LIGHTNESS_SET
        assert struct.unpack_from("<H", params)[0] == 0xFFFF

    @pytest.mark.asyncio
    async def test_tid_increments(self) -> None:
        light = _light()
        await light.send_lightness(1)
        await light.send_lightness(2)
        assert _sent_access(light, 0)[2][2] + 1 == _sent_access(light, 1)[2][2]

    @pytest.mark.asyncio
    async def test_set_without_status_does_not_raise(self) -> None:
        light = _light()
        await light.send_ctl(100, 3000)  # no device reply → timeout swallowed

    @pytest.mark.asyncio
    async def test_write_failure_raises(self) -> None:
        light = _light()
        light._client = None
        with pytest.raises(SIGMeshError):
            await light.send_ctl(100, 3000)


class TestSIGMeshLightStatus:
    @pytest.mark.asyncio
    async def test_ctl_status_round_trip_updates_state(self) -> None:
        light = _light(temp_range_k=(2700, 6500))
        seen: list[SIGLightState] = []
        light.register_light_callback(seen.append)
        await light._process_notify(
            _device_status(
                struct.pack(">H", OP_LIGHT_CTL_STATUS) + struct.pack("<HH", 0x4000, 3200)
            )
        )
        assert seen and seen[-1].temperature_k == 3200
        assert seen[-1].lightness == 0x4000
        assert seen[-1].is_on is None  # on/off only from Generic OnOff
        assert seen[-1].mode == MODE_WHITE

    @pytest.mark.asyncio
    async def test_hsl_status_infers_color_when_mode_unknown(self) -> None:
        light = _light()
        await light._process_notify(
            _device_status(struct.pack(">H", OP_LIGHT_HSL_STATUS) + struct.pack("<HHH", 1, 2, 3))
        )
        assert light.light_state.mode == MODE_COLOR
        assert (light.light_state.hue, light.light_state.saturation) == (2, 3)

    @pytest.mark.asyncio
    async def test_hsl_status_does_not_override_commanded_white(self) -> None:
        light = _light()
        await light.send_ctl(100, 3000)
        await light._process_notify(
            _device_status(struct.pack(">H", OP_LIGHT_HSL_STATUS) + struct.pack("<HHH", 1, 2, 3))
        )
        assert light.light_state.mode == MODE_WHITE

    @pytest.mark.asyncio
    async def test_onoff_status_reaches_both_callback_kinds(self) -> None:
        light = _light()
        onoff: list[bool] = []
        light.register_onoff_callback(onoff.append)
        await light._process_notify(_device_status(b"\x82\x04\x00"))
        assert onoff == [False]
        assert light.light_state.is_on is False

    @pytest.mark.asyncio
    async def test_lightness_status_does_not_set_on_off(self) -> None:
        light = _light()
        await light._process_notify(_device_status(b"\x82\x4e\x00\x00"))
        assert light.light_state.lightness == 0
        assert light.light_state.is_on is None

    @pytest.mark.asyncio
    async def test_off_bulb_reporting_colour_lightness_stays_off(self) -> None:
        """Hardware: switched-off Tuya bulb still reports HSL lightness 50%."""
        light = _light()
        await light._process_notify(_device_status(b"\x82\x04\x00", seq=900))
        await light._process_notify(
            _device_status(
                struct.pack(">H", OP_LIGHT_HSL_STATUS)
                + struct.pack("<HHH", 0x7FFF, 0xAAAA, 0xFFFF),
                seq=901,
            )
        )
        assert light.light_state.is_on is False
        assert light.light_state.lightness == 0x7FFF

    @pytest.mark.asyncio
    async def test_callback_error_is_contained(self) -> None:
        light = _light()

        def boom(_s: SIGLightState) -> None:
            raise RuntimeError("x")

        light.register_light_callback(boom)
        await light._process_notify(_device_status(b"\x82\x4e\x00\x10"))
        assert light.light_state.lightness == 0x1000


class TestSegmentedReceive:
    @pytest.mark.asyncio
    async def test_composition_segments_acked_and_parsed(self) -> None:
        light = _light()
        keys = _keys()
        payload = bytes([OP_CONFIG_COMPOSITION_STATUS]) + _composition_params()
        segs = make_access_segmented(keys.dev_key, _NODE, _US, 9000, 0, payload)
        for seq, transport in segs:
            net = encrypt_network_pdu(
                keys.enc_key,
                keys.priv_key,
                keys.nid,
                ctl=0,
                ttl=5,
                seq=seq,
                src=_NODE,
                dst=_US,
                transport_pdu=transport,
            )
            await light._process_notify(make_proxy_pdu(net))
        assert light._composition is not None and len(light._composition.elements) == 6
        # One Segment Ack (CTL=1) written back to the node
        written = light._client.write_gatt_char.await_args_list
        assert len(written) == 1
        net = decrypt_network_pdu(
            keys.enc_key, keys.priv_key, keys.nid, parse_proxy_pdu(written[0].args[1]).payload
        )
        assert net is not None and net.ctl == 1 and net.dst == _NODE
        assert net.transport_pdu[0] == 0x00
        # Retransmitted segment is re-acked, not re-dispatched
        light._composition = None
        seq, transport = segs[0]
        net_pdu = encrypt_network_pdu(
            keys.enc_key,
            keys.priv_key,
            keys.nid,
            ctl=0,
            ttl=5,
            seq=seq,
            src=_NODE,
            dst=_US,
            transport_pdu=transport,
        )
        await light._process_notify(make_proxy_pdu(net_pdu))
        assert light._composition is None
        assert len(light._client.write_gatt_char.await_args_list) == 2

    @pytest.mark.asyncio
    async def test_long_payload_sent_segmented(self) -> None:
        light = _light()
        await light._send_access(config_model_pub_set(_NODE, _US, 0, 0x1000), use_dev_key=True)
        assert light._client.write_gatt_char.await_count == 2


# ============================================================
# configure_sig_node
# ============================================================


def _config_device(*, appkey_ok: bool = True, composition: bytes | None = None) -> MagicMock:
    dev = MagicMock()
    dev._target_addr = _NODE
    dev.send_config_appkey_add = AsyncMock(return_value=appkey_ok)
    if composition is None:
        dev.get_composition_data = AsyncMock(side_effect=SIGMeshError("Timeout"))
    else:
        dev.get_composition_data = AsyncMock(return_value=composition)
    dev.send_config_model_app_bind = AsyncMock(return_value=True)
    dev.send_config_model_app_bind_vendor = AsyncMock(return_value=True)
    dev.send_config_model_pub_set = AsyncMock(return_value=True)
    return dev


class TestConfigureNode:
    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("tuya_ble_mesh.sig_mesh_light._CONFIG_STEP_DELAY", 0)

    @pytest.mark.asyncio
    async def test_binds_all_server_models_per_element(self) -> None:
        dev = _config_device(composition=_composition_params())
        result = await configure_sig_node(dev, b"\x01" * 16)
        calls = {(c.args[0], c.args[2]) for c in dev.send_config_model_app_bind.await_args_list}
        assert (_NODE, 0x1307) in calls
        assert (_NODE + 1, 0x1306) in calls
        assert (_NODE + 3, 0x130B) in calls
        assert not any(m == 0x0000 for _a, m in calls)  # Config Server never app-bound
        assert (_NODE, 0x0002) in calls  # Health Server bound (Attention uses app key)
        dev.send_config_model_app_bind_vendor.assert_awaited_once_with(_NODE, 0, 0x07D0, 0x0004)
        assert result.composition is not None and not result.failed_models

    @pytest.mark.asyncio
    async def test_fallback_when_no_composition(self) -> None:
        dev = _config_device()
        result = await configure_sig_node(dev, b"\x01" * 16)
        dev.send_config_model_app_bind.assert_awaited_once_with(_NODE, 0, 0x1000)
        assert result.composition is None

    @pytest.mark.asyncio
    async def test_appkey_rejected_raises(self) -> None:
        dev = _config_device(appkey_ok=False)
        with pytest.raises(SIGMeshError):
            await configure_sig_node(dev, b"\x01" * 16)

    @pytest.mark.asyncio
    async def test_failed_bind_reported(self) -> None:
        dev = _config_device(composition=_composition_params())
        dev.send_config_model_app_bind = AsyncMock(side_effect=SIGMeshError("Timeout"))
        result = await configure_sig_node(dev, b"\x01" * 16, bind_vendor_models=False)
        assert result.failed_models and not result.bound_models

    @pytest.mark.asyncio
    async def test_publication_configured(self) -> None:
        dev = _config_device(composition=_composition_params())
        await configure_sig_node(dev, b"\x01" * 16, publish_to=_US)
        models = [c.args[3] for c in dev.send_config_model_pub_set.await_args_list]
        assert models == [0x1000, 0x1303, 0x1307]

    @pytest.mark.asyncio
    async def test_link_drop_mid_bind_reconnects_and_resumes(self) -> None:
        """A dropped proxy link reconnects and retries the same bind."""
        dev = _config_device(composition=_composition_params())
        dev.is_connected = True
        calls = 0

        async def bind(addr: int, app_idx: int, model: int) -> bool:
            nonlocal calls
            calls += 1
            if calls == 3:
                dev.is_connected = False
                raise DisconnectedError("link lost")
            return True

        async def reconnect(**_kwargs: object) -> None:
            dev.is_connected = True

        dev.send_config_model_app_bind = AsyncMock(side_effect=bind)
        dev.connect = AsyncMock(side_effect=reconnect)

        result = await configure_sig_node(dev, b"\x01" * 16, bind_vendor_models=False)

        dev.connect.assert_awaited_once()
        assert not result.failed_models
        # The bind that hit the drop was retried, not skipped
        assert calls == len(result.bound_models) + 1

    @pytest.mark.asyncio
    async def test_link_drop_unrecoverable_raises(self) -> None:
        dev = _config_device(composition=_composition_params())
        dev.is_connected = True

        async def bind(addr: int, app_idx: int, model: int) -> bool:
            dev.is_connected = False
            raise DisconnectedError("link lost")

        dev.send_config_model_app_bind = AsyncMock(side_effect=bind)
        dev.connect = AsyncMock(side_effect=MeshConnectionError("not found"))

        with pytest.raises(MeshConnectionError):
            await configure_sig_node(dev, b"\x01" * 16, bind_vendor_models=False)


class TestInactiveModelStatus:
    """Hardware: in white mode the bulb reports HSL lightness 0 (and vice versa)."""

    @pytest.mark.asyncio
    async def test_hsl_zero_in_white_mode_does_not_turn_off(self) -> None:
        light = _light()
        await light._process_notify(
            _device_status(
                struct.pack(">H", OP_LIGHT_CTL_STATUS) + struct.pack("<HH", 0xFFFF, 6176), seq=600
            )
        )
        await light._process_notify(
            _device_status(
                struct.pack(">H", OP_LIGHT_HSL_STATUS) + struct.pack("<HHH", 0, 0, 0), seq=601
            )
        )
        st = light.light_state
        assert st.is_on is None and st.lightness == 0xFFFF and st.mode == MODE_WHITE

    @pytest.mark.asyncio
    async def test_ctl_zero_in_color_mode_does_not_turn_off(self) -> None:
        light = _light()
        await light.send_hsl(0x8000, 0x1000, 0xFFFF)
        await light._process_notify(
            _device_status(
                struct.pack(">H", OP_LIGHT_CTL_STATUS) + struct.pack("<HH", 0, 3000), seq=602
            )
        )
        assert light.light_state.mode == MODE_COLOR
        assert light.light_state.is_on is not False


class TestCTLTemperature:
    def test_temperature_set_layout(self) -> None:
        from tuya_ble_mesh.sig_mesh_protocol_codec import light_ctl_temperature_set

        payload = light_ctl_temperature_set(2700, 5)
        assert payload == b"\x82\x64" + struct.pack("<HhB", 2700, 0, 5)
        with pytest.raises(ProtocolError):
            light_ctl_temperature_set(100, 0)

    @pytest.mark.asyncio
    async def test_temperature_status_updates_state(self) -> None:
        light = _light(temp_range_k=(2700, 6500))
        await light._process_notify(
            _device_status(b"\x82\x66" + struct.pack("<Hh", 3100, 0), seq=700)
        )
        assert light.light_state.temperature_k == 3100


class TestTuyaTemperatureScaling:
    """Hardware: Tuya maps mesh temperature linearly over 800-20000 K."""

    @pytest.mark.asyncio
    async def test_display_range_mapped_to_full_mesh_range(self) -> None:
        light = _light()  # no device-reported range -> scaling on
        await light.send_temperature(2700)
        assert struct.unpack_from("<H", _sent_access(light)[2])[0] == 800
        await light.send_temperature(6500)
        assert struct.unpack_from("<H", _sent_access(light)[2])[0] == 20000

    @pytest.mark.asyncio
    async def test_status_mapped_back(self) -> None:
        light = _light()
        await light._process_notify(
            _device_status(b"\x82\x66" + struct.pack("<Hh", 20000, 0), seq=800)
        )
        assert light.light_state.temperature_k == 6500

    @pytest.mark.asyncio
    async def test_reported_range_disables_scaling(self) -> None:
        light = _light(temp_range_k=(2700, 6500))
        await light.send_temperature(4000)
        assert struct.unpack_from("<H", _sent_access(light)[2])[0] == 4000


class TestTransitionsAndAttention:
    @pytest.mark.parametrize(
        ("seconds", "encoded"),
        [(0, 0x00), (0.5, 0x05), (6.2, 0x3E), (10, 0x4A), (90, 0x89), (1800, 0xC3), (1e9, 0xFE)],
    )
    def test_encode_transition_time(self, seconds: float, encoded: int) -> None:
        from tuya_ble_mesh.sig_mesh_protocol_codec import encode_transition_time

        assert encode_transition_time(seconds) == encoded

    def test_decode_transition_time(self) -> None:
        from tuya_ble_mesh.sig_mesh_protocol_codec import decode_transition_time

        assert decode_transition_time(0x4A) == 10.0
        assert decode_transition_time(0x3F) == 0.0

    @pytest.mark.asyncio
    async def test_hsl_with_transition_appends_tt_and_delay(self) -> None:
        light = _light()
        await light.send_hsl(0x7FFF, 0, 0xFFFF, transition_s=2.0)
        _dst, opcode, params = _sent_access(light)
        assert opcode == OP_LIGHT_HSL_SET
        assert params[-2:] == bytes([0x14, 0x00])  # 20 x 100 ms, no delay

    @pytest.mark.asyncio
    async def test_no_transition_keeps_short_payload(self) -> None:
        light = _light()
        await light.send_lightness(100)
        assert len(_sent_access(light)[2]) == 3  # lightness(2) + tid

    @pytest.mark.asyncio
    async def test_onoff_fade(self) -> None:
        light = _light()
        await light.send_onoff(False, transition_s=3)
        _dst, opcode, params = _sent_access(light)
        assert opcode == 0x8202 and params[0] == 0 and params[2:] == bytes([0x1E, 0])

    @pytest.mark.asyncio
    async def test_attention_binds_health_server_when_unanswered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "tuya_ble_mesh.sig_mesh_device_commands.SIG_MESH_ONOFF_RESPONSE_TIMEOUT", 0.01
        )
        light = _light()
        light.send_config_model_app_bind = AsyncMock(return_value=True)  # type: ignore[method-assign]
        ok = await light.send_attention(5)
        assert ok is False  # no device in the test answers
        light.send_config_model_app_bind.assert_awaited_once_with(_NODE, 0, 0x0002)
        _dst, opcode, params = _sent_access(light)
        assert opcode == 0x8005 and params == bytes([5])
