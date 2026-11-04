"""Unit tests for the HA side of SIG Mesh light support.

Covers:
- Discovery classification from the Tuya Device UUID (VOLT G4 advertisement)
- Device factory for sig_light entries
- Coordinator: SIG light state updates and reserving sequence store
- TuyaBLEMeshSIGLight entity: state mapping and command selection
- sig_light config flow step
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))

from homeassistant.components.light import ColorMode  # noqa: E402
from tuya_ble_mesh.sig_mesh_light import SIGLightState, SIGMeshLight  # noqa: E402

from custom_components.tuya_ble_mesh.config_flow import TuyaBLEMeshConfigFlow  # noqa: E402
from custom_components.tuya_ble_mesh.config_flow_discovery import (  # noqa: E402
    is_tuya_device_uuid,
    sig_device_type_from_advertisement,
)
from custom_components.tuya_ble_mesh.config_flow_sig import ProvisionedNode  # noqa: E402
from custom_components.tuya_ble_mesh.const import (  # noqa: E402
    CONF_DEVICE_TYPE,
    CONF_MODEL_ELEMENTS,
    CONF_TEMP_MAX_K,
    CONF_TEMP_MIN_K,
    DEVICE_TYPE_SIG_LIGHT,
    DEVICE_TYPE_SIG_PLUG,
    SIG_MESH_PROV_UUID,
)
from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    TuyaBLEMeshCoordinator,
    TuyaBLEMeshDeviceState,
    _ReservingSeqStore,
)
from custom_components.tuya_ble_mesh.device_factory import create_device  # noqa: E402
from custom_components.tuya_ble_mesh.light_sig import (  # noqa: E402
    HSL_LIGHTNESS_MAX,
    TuyaBLEMeshSIGLight,
    brightness_to_hsl_lightness,
    brightness_to_lightness,
    hs_to_mesh,
    lightness_to_brightness,
)

# 0x1827 service data advertised by the VOLT G4 RGBCW bulb (from the HA
# bluetooth cache): MAC dc2353dab3d4, category 0x5115, PID "key9aghe", OOB 0000
_VOLT_G4_SERVICE_DATA = bytes.fromhex("dc2353dab3d451156b657939616768650000")
_KEYS = {
    "net_key": "00112233445566778899aabbccddeeff",  # pragma: allowlist secret
    "dev_key": "ffeeddccbbaa99887766554433221100",  # pragma: allowlist secret
    "app_key": "aabbccddeeff00112233445566778899",  # pragma: allowlist secret
}


# ============================================================
# Discovery classification
# ============================================================


class TestDiscoveryClassification:
    def test_volt_g4_is_light(self) -> None:
        info = MagicMock(service_data={SIG_MESH_PROV_UUID: _VOLT_G4_SERVICE_DATA})
        assert sig_device_type_from_advertisement(info) == DEVICE_TYPE_SIG_LIGHT

    def test_electrical_category_is_plug(self) -> None:
        data = bytearray(_VOLT_G4_SERVICE_DATA)
        data[6] = 0x21
        info = MagicMock(service_data={SIG_MESH_PROV_UUID: bytes(data)})
        assert sig_device_type_from_advertisement(info) == DEVICE_TYPE_SIG_PLUG

    @pytest.mark.parametrize("service_data", [None, {}, {SIG_MESH_PROV_UUID: b"\x01\x02"}])
    def test_missing_data_defaults_to_plug(self, service_data: Any) -> None:
        info = MagicMock(service_data=service_data)
        assert sig_device_type_from_advertisement(info) == DEVICE_TYPE_SIG_PLUG

    def test_tuya_uuid_starts_with_mac(self) -> None:
        info = MagicMock(
            address="DC:23:53:DA:B3:D4", service_data={SIG_MESH_PROV_UUID: _VOLT_G4_SERVICE_DATA}
        )
        assert is_tuya_device_uuid(info)

    @pytest.mark.parametrize(
        ("address", "service_data"),
        [
            ("AA:BB:CC:DD:EE:FF", {SIG_MESH_PROV_UUID: _VOLT_G4_SERVICE_DATA}),
            ("DC:23:53:DA:B3:D4", {SIG_MESH_PROV_UUID: b"\xdc\x23"}),
            ("DC:23:53:DA:B3:D4", {}),
            ("DC:23:53:DA:B3:D4", None),
            ("not-a-mac", {SIG_MESH_PROV_UUID: _VOLT_G4_SERVICE_DATA}),
        ],
    )
    def test_other_vendor_uuid_rejected(self, address: str, service_data: Any) -> None:
        info = MagicMock(address=address, service_data=service_data)
        assert not is_tuya_device_uuid(info)


# ============================================================
# Device factory
# ============================================================


class TestFactory:
    def test_creates_sig_light_with_layout(self) -> None:
        data = {
            "mac_address": "DC:23:53:DA:B3:D4",
            CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_LIGHT,
            "unicast_target": "00B0",
            "unicast_our": "0001",
            CONF_MODEL_ELEMENTS: {"1307": 0, "1306": 1, "bogus": "x"},
            CONF_TEMP_MIN_K: 2200,
            CONF_TEMP_MAX_K: 6000,
            **_KEYS,
        }
        dev = create_device(DEVICE_TYPE_SIG_LIGHT, "DC:23:53:DA:B3:D4", data)
        assert isinstance(dev, SIGMeshLight)
        assert dev._model_elements == {0x1307: 0, 0x1306: 1}
        assert dev.temp_range_k == (2200, 6000)
        assert dev._element_addr(0x1306) == 0x00B1

    def test_sig_light_missing_keys_raises(self) -> None:
        with pytest.raises(ValueError):
            create_device(DEVICE_TYPE_SIG_LIGHT, "AA:BB:CC:DD:EE:FF", {})

    def test_sig_plug_factory_no_longer_crashes(self) -> None:
        """Regression: _create_sig_plug passed an unsupported kwarg (TypeError)."""
        data = {"unicast_target": "00B0", **_KEYS}
        dev = create_device(DEVICE_TYPE_SIG_PLUG, "AA:BB:CC:DD:EE:FF", data, MagicMock())
        assert dev.address == "AA:BB:CC:DD:EE:FF"


# ============================================================
# Coordinator
# ============================================================


def _coordinator_with_light() -> tuple[TuyaBLEMeshCoordinator, SIGMeshLight]:
    data = {"unicast_target": "00B0", **_KEYS}
    light = create_device(DEVICE_TYPE_SIG_LIGHT, "DC:23:53:DA:B3:D4", data)
    assert isinstance(light, SIGMeshLight)
    return TuyaBLEMeshCoordinator(light), light


class TestCoordinatorSIGLight:
    def test_capability_detected(self) -> None:
        coord, _light = _coordinator_with_light()
        assert coord.capabilities.has_sig_light is True

    def test_light_update_maps_fields(self) -> None:
        coord, _light = _coordinator_with_light()
        coord._on_sig_light_update(
            SIGLightState(
                is_on=True,
                lightness=0x8000,
                temperature_k=3000,
                hue=0x5555,
                saturation=0xFFFF,
                mode="color",
            )
        )
        st = coord.state
        assert st.is_on is True and st.available is True
        assert st.lightness == 0x8000 and st.color_temp_kelvin == 3000
        assert st.hs_color == (120.0, 100.0)
        assert st.light_mode == "color"

    def test_zero_lightness_keeps_last_brightness(self) -> None:
        coord, _light = _coordinator_with_light()
        coord._on_sig_light_update(SIGLightState(is_on=True, lightness=0x4000))
        coord._on_sig_light_update(SIGLightState(is_on=False, lightness=0))
        assert coord.state.is_on is False
        assert coord.state.lightness == 0x4000


class TestReservingSeqStore:
    def test_reserves_in_blocks(self) -> None:
        saved: list[int] = []
        store = _ReservingSeqStore(100, saved.append, block=10)
        store.set_seq(101)  # crosses initial reservation (100)
        assert saved == [111]
        for seq in range(102, 111):
            store.set_seq(seq)
        assert saved == [111]
        store.set_seq(111)
        assert saved == [111, 121]
        assert store.get_seq() == 111 and store.reserved == 121

    @pytest.mark.asyncio
    async def test_device_allocation_triggers_reservation(self) -> None:
        _coord, light = _coordinator_with_light()
        saved: list[int] = []
        light.set_seq_store(_ReservingSeqStore(5000, saved.append, block=64))
        await light._next_seq()
        assert saved == [5065]


# ============================================================
# Light entity
# ============================================================


def _entity(**state: Any) -> tuple[TuyaBLEMeshSIGLight, MagicMock]:
    coord = MagicMock()
    coord.state = TuyaBLEMeshDeviceState(available=True, **state)
    coord.device = MagicMock()
    coord.device.address = "DC:23:53:DA:B3:D4"
    coord.device.temp_range_k = (2700, 6500)
    coord.device.send_hsl = AsyncMock()
    coord.device.send_ctl = AsyncMock()
    coord.device.send_lightness = AsyncMock()
    coord.device.send_power = AsyncMock()

    async def _run(func: Any, **_kw: Any) -> None:
        await func()

    coord.send_command_with_retry = AsyncMock(side_effect=_run)
    return TuyaBLEMeshSIGLight(coord, "entry"), coord


class TestConversions:
    def test_brightness_round_trip(self) -> None:
        assert brightness_to_lightness(255) == 0xFFFF
        assert brightness_to_lightness(0) == 0
        for b in (1, 64, 128, 255):
            assert lightness_to_brightness(brightness_to_lightness(b)) == b
        assert lightness_to_brightness(1) == 1  # never rounds an on light to 0

    def test_hs_to_mesh(self) -> None:
        assert hs_to_mesh((0.0, 100.0)) == (0, 0xFFFF)
        assert hs_to_mesh((360.0, 0.0)) == (0, 0)
        assert hs_to_mesh((180.0, 50.0)) == (round(0xFFFF / 2), round(0xFFFF / 2))


class TestSIGLightEntity:
    def test_white_mode_state(self) -> None:
        ent, _ = _entity(is_on=True, lightness=0xFFFF, color_temp_kelvin=4000, light_mode="white")
        assert ent.color_mode == ColorMode.COLOR_TEMP
        assert ent.brightness == 255
        assert ent.color_temp_kelvin == 4000
        assert ent.hs_color is None
        assert ent.supported_color_modes == {ColorMode.COLOR_TEMP, ColorMode.HS}
        assert (ent.min_color_temp_kelvin, ent.max_color_temp_kelvin) == (2700, 6500)

    def test_color_mode_state(self) -> None:
        ent, _ = _entity(is_on=True, lightness=0x8000, hs_color=(10.0, 90.0), light_mode="color")
        assert ent.color_mode == ColorMode.HS
        assert ent.hs_color == (10.0, 90.0)
        assert ent.color_temp_kelvin is None

    def test_off_has_no_brightness(self) -> None:
        ent, _ = _entity(is_on=False, lightness=0x8000)
        assert ent.brightness is None

    @pytest.mark.asyncio
    async def test_turn_on_hs_sends_hsl(self) -> None:
        ent, coord = _entity(is_on=False, lightness=0x8000)
        await ent.async_turn_on(hs_color=(120.0, 100.0))
        # Light is off -> no current brightness -> full colour (HSL L = 50%)
        coord.device.send_hsl.assert_awaited_once_with(
            HSL_LIGHTNESS_MAX, round(0xFFFF / 3), 0xFFFF, None
        )
        sent = coord.assume_state.call_args.args[1]
        assert sent["light_mode"] == "color" and sent["is_on"] is True

    @pytest.mark.asyncio
    async def test_turn_on_kelvin_with_brightness_sends_ctl_clamped(self) -> None:
        ent, coord = _entity()
        await ent.async_turn_on(color_temp_kelvin=9000, brightness=128)
        coord.device.send_ctl.assert_awaited_once_with(brightness_to_lightness(128), 6500, None)

    @pytest.mark.asyncio
    async def test_turn_on_brightness_only_sends_lightness(self) -> None:
        ent, coord = _entity(light_mode="color")
        await ent.async_turn_on(brightness=10)
        coord.device.send_lightness.assert_awaited_once_with(brightness_to_lightness(10), None)
        coord.device.send_hsl.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_plain_turn_on_and_off_use_onoff(self) -> None:
        ent, coord = _entity()
        await ent.async_turn_on()
        coord.device.send_power.assert_awaited_once_with(True)
        await ent.async_turn_off()
        coord.device.send_power.assert_awaited_with(False)


# ============================================================
# Config flow
# ============================================================


def _make_flow() -> TuyaBLEMeshConfigFlow:
    flow = TuyaBLEMeshConfigFlow()
    flow.context = {"source": "bluetooth"}
    hass = MagicMock()
    hass.config_entries.async_entries = MagicMock(return_value=[])
    hass.config_entries.async_entry_for_domain_unique_id = MagicMock(return_value=None)
    hass.config_entries.flow.async_progress_by_handler = MagicMock(return_value=[])
    flow.hass = hass
    flow.async_set_unique_id = AsyncMock()  # type: ignore[method-assign]
    flow._abort_if_unique_id_configured = MagicMock()  # type: ignore[method-assign]
    return flow


class TestSIGLightFlow:
    @pytest.mark.asyncio
    async def test_shows_confirm_form(self) -> None:
        flow = _make_flow()
        flow._discovery_info = {"address": "DC:23:53:DA:B3:D4", "name": ""}
        result = await flow.async_step_sig_light(None)
        assert result["type"] == "form" and result["step_id"] == "sig_light"

    @pytest.mark.asyncio
    async def test_creates_sig_light_entry_with_layout(self) -> None:
        flow = _make_flow()
        flow._discovery_info = {"address": "DC:23:53:DA:B3:D4", "name": ""}
        node = ProvisionedNode(
            unicast=0x00B0,
            num_elements=6,
            model_elements={"1307": 0, "1306": 1},
            temp_range_k=(2700, 6500),
            **_KEYS,
        )
        with patch(
            "custom_components.tuya_ble_mesh.config_flow_sig.run_provision",
            new=AsyncMock(return_value=node),
        ) as prov:
            result = await flow.async_step_sig_light({})
        prov.assert_awaited_once()
        assert prov.await_args.args[2] == DEVICE_TYPE_SIG_LIGHT
        assert result["type"] == "create_entry"
        data = result["data"]
        assert data[CONF_DEVICE_TYPE] == DEVICE_TYPE_SIG_LIGHT
        assert data[CONF_MODEL_ELEMENTS] == {"1307": 0, "1306": 1}
        assert (data[CONF_TEMP_MIN_K], data[CONF_TEMP_MAX_K]) == (2700, 6500)
        assert data["element_count"] == 6
        assert result["title"].startswith("Smart Light")

    @pytest.mark.asyncio
    async def test_configuration_failure_shows_reset_error(self) -> None:
        from custom_components.tuya_ble_mesh.config_flow_sig import NodeConfigurationError

        flow = _make_flow()
        flow._discovery_info = {"address": "DC:23:53:DA:B3:D4", "name": ""}
        with patch(
            "custom_components.tuya_ble_mesh.config_flow_sig.run_provision",
            new=AsyncMock(side_effect=NodeConfigurationError("x")),
        ):
            result = await flow.async_step_sig_light({})
        assert result["type"] == "form"
        assert result["errors"] == {"base": "configuration_failed"}

    @pytest.mark.asyncio
    async def test_discovery_routes_volt_g4_to_sig_light(self) -> None:
        from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

        flow = _make_flow()
        info = MagicMock(spec=BluetoothServiceInfoBleak)
        info.address = "DC:23:53:DA:B3:D4"
        info.name = ""
        info.rssi = -60
        info.service_uuids = [SIG_MESH_PROV_UUID]
        info.service_data = {SIG_MESH_PROV_UUID: _VOLT_G4_SERVICE_DATA}
        with patch(
            "homeassistant.components.bluetooth.async_ble_device_from_address",
            return_value=MagicMock(),
        ):
            result = await flow.async_step_bluetooth(info)
        assert result["step_id"] == "sig_light"
        assert flow.context["title_placeholders"]["category"] == "Smart Light"


# ============================================================
# Regressions found on hardware (VOLT G4, 2026-10-07)
# ============================================================


class TestHardwareRegressions:
    def test_entry_data_carries_next_seq(self) -> None:
        """Provisioning uses ~30 SEQs; the entry must continue after them."""
        node = ProvisionedNode(unicast=0x00B0, num_elements=6, next_seq=31, **_KEYS)
        assert node.entry_data()["seq_start"] == 31

    @pytest.mark.asyncio
    async def test_first_start_uses_seq_start(self) -> None:
        _coord, light = _coordinator_with_light()
        entry = MagicMock()
        entry.data = {"seq_start": 31}
        coord = TuyaBLEMeshCoordinator(light, hass=MagicMock(), entry_id="e1", entry=entry)
        store = MagicMock()
        store.async_load = AsyncMock(return_value=None)
        with patch("homeassistant.helpers.storage.Store", return_value=store):
            await coord._load_seq()
        assert light.get_seq() > 31

    def test_apply_updated_data_handles_sync_return(self) -> None:
        """async_set_updated_data is sync in current HA (returned None -> TypeError)."""
        coord, _light = _coordinator_with_light()
        coord._entry = MagicMock()
        coord._hass = MagicMock()
        with patch.object(coord, "async_set_updated_data", new=MagicMock(return_value=None)):
            coord._apply_updated_data()
        coord._entry.async_create_background_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_brightness_color_is_half_hsl_lightness(self) -> None:
        """Hardware: HSL lightness 100% renders white; full colour is at 50%."""
        ent, coord = _entity(is_on=True, lightness=0xFFFF, light_mode="white")
        await ent.async_turn_on(hs_color=(0.0, 100.0), brightness=255)
        assert coord.device.send_hsl.await_args.args[0] == HSL_LIGHTNESS_MAX

    @pytest.mark.asyncio
    async def test_brightness_change_in_color_mode_keeps_color(self) -> None:
        ent, coord = _entity(
            is_on=True, lightness=HSL_LIGHTNESS_MAX, hs_color=(240.0, 100.0), light_mode="color"
        )
        assert ent.brightness == 255
        await ent.async_turn_on(brightness=64)
        coord.device.send_lightness.assert_not_awaited()
        lightness, hue, sat, _transition = coord.device.send_hsl.await_args.args
        assert lightness == brightness_to_hsl_lightness(64)
        assert (hue, sat) == hs_to_mesh((240.0, 100.0))


class TestFadeFromOff:
    """Hardware: Tuya fades on Generic OnOff but not on a Set that turns the light on."""

    @pytest.mark.asyncio
    async def test_fade_on_from_off_uses_onoff_first(self) -> None:
        ent, coord = _entity(is_on=False, lightness=0x7FFF, light_mode="color")
        coord.device.send_onoff = AsyncMock()
        await ent.async_turn_on(hs_color=(240.0, 100.0), brightness=255, transition=5)
        coord.device.send_onoff.assert_awaited_once_with(True, 5)
        assert coord.device.send_hsl.await_args.args[3] == 5

    @pytest.mark.asyncio
    async def test_fade_on_without_target_only_onoff(self) -> None:
        ent, coord = _entity(is_on=False)
        coord.device.send_onoff = AsyncMock()
        await ent.async_turn_on(transition=3)
        coord.device.send_onoff.assert_awaited_once_with(True, 3)
        coord.device.send_power.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_extra_onoff_when_already_on(self) -> None:
        ent, coord = _entity(is_on=True, lightness=0x7FFF, light_mode="color")
        coord.device.send_onoff = AsyncMock()
        await ent.async_turn_on(hs_color=(0.0, 100.0), transition=5)
        coord.device.send_onoff.assert_not_awaited()
