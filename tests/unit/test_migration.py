"""Phase 5: moving legacy SIG entries into the shared network."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "custom_components" / "tuya_ble_mesh" / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_mesh_network_ha import FakeEntry  # noqa: E402

from custom_components.tuya_ble_mesh import config_flow_network, migration  # noqa: E402
from custom_components.tuya_ble_mesh.const import (  # noqa: E402
    CONF_DEVICE_TYPE,
    CONF_MAC_ADDRESS,
    CONF_NET_KEY,
    DEVICE_TYPE_SIG_LIGHT,
)
from custom_components.tuya_ble_mesh.migration import (  # noqa: E402
    MigrationError,
    async_migrate_legacy_entry,
    is_legacy_sig_entry,
)

MAC = "DC:23:53:DA:B3:D4"


def _legacy(connected: bool = True, acked: bool = True) -> SimpleNamespace:
    device = MagicMock()
    device.address = MAC
    device.is_connected = connected
    device.send_config_node_reset = AsyncMock(return_value=acked)
    return SimpleNamespace(
        entry_id="legacy_id",
        title="Smart Light DA:B3:D4",
        data={
            CONF_MAC_ADDRESS: MAC,
            CONF_DEVICE_TYPE: DEVICE_TYPE_SIG_LIGHT,
            CONF_NET_KEY: "aa" * 16,
        },
        runtime_data=SimpleNamespace(coordinator=SimpleNamespace(device=device)),
    )


def _hass() -> MagicMock:
    hass = MagicMock()
    hass.config_entries.async_unload = AsyncMock(return_value=True)
    hass.config_entries.async_reload = AsyncMock(return_value=True)
    hass.config_entries.async_remove = AsyncMock()
    return hass


class Harness:
    """Patches the network side and registries; records what happened."""

    def __init__(self, *, unprovisioned_now: bool = False, appears: bool = True) -> None:
        self.network = FakeEntry({})
        self.device = SimpleNamespace(id="dev1")
        self.hub = SimpleNamespace(id="hub1")
        self.dev_reg = MagicMock()
        self.dev_reg.async_get_device = MagicMock(
            side_effect=lambda identifiers: (
                self.hub if ("tuya_ble_mesh", "abc123") in identifiers else self.device
            )
        )
        self.ent_reg = MagicMock()
        self.entities = [
            SimpleNamespace(entity_id="light.bulb", unique_id=f"{MAC}_light"),
            SimpleNamespace(entity_id="update.bulb_firmware", unique_id="legacy_id_firmware"),
        ]
        self.add_node = AsyncMock(side_effect=self._add_node)
        self.unprovisioned_now = unprovisioned_now
        self.appears = appears

    async def _add_node(self, hass: Any, mac: str, device_type: str, title: str, *, on_added: Any):
        on_added(self.network, "sub_new")
        return {}

    def __enter__(self) -> Harness:
        from contextlib import ExitStack

        self._stack = ExitStack()
        s = self._stack
        s.enter_context(
            patch.object(
                config_flow_network,
                "async_ensure_network_entry",
                AsyncMock(return_value=self.network),
            )
        )
        s.enter_context(
            patch.object(config_flow_network, "async_add_node_to_network", self.add_node)
        )
        s.enter_context(
            patch.object(migration, "_unprovisioned_now", return_value=self.unprovisioned_now)
        )
        s.enter_context(
            patch.object(
                migration, "_async_wait_unprovisioned", AsyncMock(return_value=self.appears)
            )
        )
        s.enter_context(
            patch("homeassistant.helpers.device_registry.async_get", return_value=self.dev_reg)
        )
        s.enter_context(
            patch("homeassistant.helpers.entity_registry.async_get", return_value=self.ent_reg)
        )
        s.enter_context(
            patch(
                "homeassistant.helpers.entity_registry.async_entries_for_config_entry",
                return_value=self.entities,
            )
        )
        self.delete_issue = s.enter_context(patch.object(migration, "async_delete_migration_issue"))
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stack.close()


class TestMigration:
    @pytest.mark.asyncio
    async def test_happy_path(self) -> None:
        hass, legacy = _hass(), _legacy()
        with Harness() as h:
            sub_id = await async_migrate_legacy_entry(hass, legacy)

        assert sub_id == "sub_new"
        legacy.runtime_data.coordinator.device.send_config_node_reset.assert_awaited_once()
        hass.config_entries.async_unload.assert_awaited_once_with("legacy_id")
        h.add_node.assert_awaited_once()
        assert h.add_node.await_args.args[1:] == (MAC, DEVICE_TYPE_SIG_LIGHT, legacy.title)
        # Device moved onto the network subentry, under the hub, off the legacy entry
        h.dev_reg.async_update_device.assert_any_call(
            "dev1",
            add_config_entry_id="net_entry",
            add_config_subentry_id="sub_new",
            via_device_id="hub1",
        )
        h.dev_reg.async_update_device.assert_any_call("dev1", remove_config_entry_id="legacy_id")
        # Entities keep their entity_id; the entry-id based unique ID is renamed
        h.ent_reg.async_update_entity.assert_any_call(
            "light.bulb", config_entry_id="net_entry", config_subentry_id="sub_new"
        )
        h.ent_reg.async_update_entity.assert_any_call(
            "update.bulb_firmware",
            config_entry_id="net_entry",
            config_subentry_id="sub_new",
            new_unique_id="sub_new_firmware",
        )
        hass.config_entries.async_remove.assert_awaited_once_with("legacy_id")
        h.delete_issue.assert_called_once_with(hass, "legacy_id")

    @pytest.mark.asyncio
    async def test_no_reset_restores_legacy_entry(self) -> None:
        hass, legacy = _hass(), _legacy(acked=False)
        with Harness(appears=False) as h, pytest.raises(MigrationError) as err:
            await async_migrate_legacy_entry(hass, legacy)
        assert err.value.reason == "reset_failed"
        hass.config_entries.async_reload.assert_awaited_once_with("legacy_id")
        h.add_node.assert_not_awaited()
        hass.config_entries.async_remove.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_acked_but_not_advertising(self) -> None:
        hass, legacy = _hass(), _legacy(acked=True)
        with Harness(appears=False), pytest.raises(MigrationError) as err:
            await async_migrate_legacy_entry(hass, legacy)
        assert err.value.reason == "not_unprovisioned"
        hass.config_entries.async_reload.assert_not_awaited()  # its old keys are gone

    @pytest.mark.asyncio
    async def test_already_unprovisioned_skips_reset(self) -> None:
        hass, legacy = _hass(), _legacy(connected=False)
        with Harness(unprovisioned_now=True) as h:
            await async_migrate_legacy_entry(hass, legacy)
        legacy.runtime_data.coordinator.device.send_config_node_reset.assert_not_awaited()
        h.add_node.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_offline_bulb_not_touched(self) -> None:
        hass, legacy = _hass(), _legacy(connected=False)
        with Harness() as h, pytest.raises(MigrationError) as err:
            await async_migrate_legacy_entry(hass, legacy)
        assert err.value.reason == "not_connected"
        hass.config_entries.async_unload.assert_not_awaited()
        h.add_node.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_provisioning_failure_keeps_legacy_entry(self) -> None:
        hass, legacy = _hass(), _legacy()
        with Harness() as h:
            h.add_node.side_effect = RuntimeError("PB-GATT failed")
            with pytest.raises(MigrationError) as err:
                await async_migrate_legacy_entry(hass, legacy)
        assert err.value.reason == "provisioning_failed"
        hass.config_entries.async_remove.assert_not_awaited()

    def test_legacy_detection(self) -> None:
        assert is_legacy_sig_entry(_legacy())
        assert not is_legacy_sig_entry(FakeEntry({}))
        plain = SimpleNamespace(data={CONF_DEVICE_TYPE: "light", CONF_MAC_ADDRESS: MAC})
        assert not is_legacy_sig_entry(plain)


class TestRepairFlow:
    @pytest.mark.asyncio
    async def test_fix_flow_for_legacy_issue(self) -> None:
        from custom_components.tuya_ble_mesh.repairs import (
            LegacyMigrationRepairFlow,
            TuyaBLEMeshRepairFlow,
            async_create_fix_flow,
        )

        flow = await async_create_fix_flow(
            MagicMock(), "legacy_sig_network_legacy_id", {"entry_id": "legacy_id"}
        )
        assert isinstance(flow, LegacyMigrationRepairFlow)
        other = await async_create_fix_flow(MagicMock(), "timeout", None)
        assert isinstance(other, TuyaBLEMeshRepairFlow)

    @pytest.mark.asyncio
    async def test_confirm_runs_migration(self) -> None:
        from custom_components.tuya_ble_mesh.repairs import LegacyMigrationRepairFlow

        legacy = _legacy()
        flow = LegacyMigrationRepairFlow("legacy_id")
        flow.hass = MagicMock()
        flow.hass.config_entries.async_get_entry = MagicMock(return_value=legacy)

        form = await flow.async_step_init()
        assert form["type"] == "form"
        assert form["description_placeholders"] == {"name": legacy.title}

        with patch.object(migration, "async_migrate_legacy_entry", AsyncMock(return_value="s")):
            done = await flow.async_step_confirm({})
        assert done["type"] == "create_entry"

        with patch.object(
            migration,
            "async_migrate_legacy_entry",
            AsyncMock(side_effect=MigrationError("reset_failed")),
        ):
            failed = await flow.async_step_confirm({})
        assert failed["errors"] == {"base": "reset_failed"}

    @pytest.mark.asyncio
    async def test_missing_entry_aborts(self) -> None:
        from custom_components.tuya_ble_mesh.repairs import LegacyMigrationRepairFlow

        flow = LegacyMigrationRepairFlow("gone")
        flow.hass = MagicMock()
        flow.hass.config_entries.async_get_entry = MagicMock(return_value=None)
        assert (await flow.async_step_confirm())["reason"] == "entry_not_found"


class TestUnprovisionedDetection:
    def _info(self, uuids: list[str], age: float) -> Any:
        import time

        return SimpleNamespace(service_uuids=uuids, time=time.monotonic() - age)

    def test_fresh_provisioning_advert(self) -> None:
        with patch(
            "homeassistant.components.bluetooth.async_last_service_info",
            return_value=self._info([migration.SIG_MESH_PROV_UUID], 2),
        ):
            assert migration._unprovisioned_now(MagicMock(), MAC)

    def test_stale_or_proxy_advert_not_trusted(self) -> None:
        for info in (
            self._info([migration.SIG_MESH_PROV_UUID], 600),  # stale cache
            self._info(["00001828-0000-1000-8000-00805f9b34fb"], 1),  # provisioned
            None,
        ):
            with patch(
                "homeassistant.components.bluetooth.async_last_service_info", return_value=info
            ):
                assert not migration._unprovisioned_now(MagicMock(), MAC)

    @pytest.mark.asyncio
    async def test_wait_uses_advert_matcher(self) -> None:
        wait = AsyncMock(return_value=MagicMock())
        with (
            patch.object(migration, "_unprovisioned_now", return_value=False),
            patch("homeassistant.components.bluetooth.async_process_advertisements", wait),
        ):
            assert await migration._async_wait_unprovisioned(MagicMock(), MAC, 5)
            predicate = wait.await_args.args[1]
            assert predicate(SimpleNamespace(service_uuids=[migration.SIG_MESH_PROV_UUID]))
            assert not predicate(SimpleNamespace(service_uuids=[]))
        with (
            patch.object(migration, "_unprovisioned_now", return_value=False),
            patch(
                "homeassistant.components.bluetooth.async_process_advertisements",
                AsyncMock(side_effect=TimeoutError),
            ),
        ):
            assert not await migration._async_wait_unprovisioned(MagicMock(), MAC, 5)
