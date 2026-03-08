"""Sequence number replay protection tests.

Verifies that SIG Mesh sequence numbers never decrease,
increment monotonically, and that persistence with safety
margin prevents replay after crash/restart.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
sys.path.insert(0, _ROOT)
sys.path.insert(0, str(Path(_ROOT) / "lib"))

from tuya_ble_mesh.sig_mesh_device import SIGMeshDevice  # noqa: E402

from custom_components.tuya_ble_mesh.coordinator import (  # noqa: E402
    _SEQ_SAFETY_MARGIN,
    TuyaBLEMeshCoordinator,
)


@pytest.mark.requires_ha
class TestSeqNeverDecreases:
    """Verify sequence number never goes backward."""

    @pytest.mark.asyncio
    async def test_seq_never_decreases_during_send(self) -> None:
        """Each _next_seq() call must return a larger value."""
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        prev = await dev._next_seq()
        for _ in range(100):
            current = await dev._next_seq()
            assert current > prev, f"seq went backward: {current} <= {prev}"
            prev = current

    @pytest.mark.asyncio
    async def test_seq_monotonic_after_set_seq(self) -> None:
        """After set_seq, sequence should continue monotonically."""
        dev = SIGMeshDevice("DC:23:4D:21:43:A5", 0x00AA, 0x0001, MagicMock())
        dev.set_seq(50000)
        prev = await dev._next_seq()
        for _ in range(100):
            current = await dev._next_seq()
            assert current == prev + 1
            prev = current


@pytest.mark.requires_ha
class TestSeqRestoreWithMargin:
    """Verify seq restore adds safety margin to prevent replay."""

    @pytest.mark.asyncio
    async def test_restored_seq_exceeds_stored(self) -> None:
        """After load_seq, device seq must be > stored value."""
        stored_seq = 5000
        device = MagicMock()
        device.address = "DC:23:4D:21:43:A5"
        device.set_seq = MagicMock()
        device.get_seq = MagicMock(return_value=stored_seq + _SEQ_SAFETY_MARGIN)
        device.firmware_version = None

        mock_hass = MagicMock()
        mock_store = MagicMock()
        mock_store.async_load = AsyncMock(return_value={"seq": stored_seq})

        coord = TuyaBLEMeshCoordinator(device, hass=mock_hass, entry_id="test_entry")

        with patch(
            "homeassistant.helpers.storage.Store",
            return_value=mock_store,
        ):
            await coord._load_seq()

        device.set_seq.assert_called_once()
        restored = device.set_seq.call_args[0][0]
        assert restored > stored_seq
        assert restored == stored_seq + _SEQ_SAFETY_MARGIN

    @pytest.mark.asyncio
    async def test_safety_margin_is_positive(self) -> None:
        """Safety margin must be > 0 to prevent replay."""
        assert _SEQ_SAFETY_MARGIN > 0

    @pytest.mark.asyncio
    async def test_no_replay_window(self) -> None:
        """Restored seq should have no overlap with previous session."""
        stored_seq = 10000
        device = MagicMock()
        device.address = "DC:23:4D:21:43:A5"
        device.set_seq = MagicMock()

        mock_hass = MagicMock()
        mock_store = MagicMock()
        mock_store.async_load = AsyncMock(return_value={"seq": stored_seq})

        coord = TuyaBLEMeshCoordinator(device, hass=mock_hass, entry_id="test_entry")

        with patch(
            "homeassistant.helpers.storage.Store",
            return_value=mock_store,
        ):
            await coord._load_seq()

        restored = device.set_seq.call_args[0][0]
        # The restored seq should be far enough ahead that even if the
        # previous session sent more messages after saving, we won't overlap
        assert restored - stored_seq >= _SEQ_SAFETY_MARGIN


@pytest.mark.requires_ha
class TestSeqPersistenceIntegrity:
    """Verify persisted seq is >= used seq."""

    @pytest.mark.asyncio
    async def test_saved_seq_matches_device_seq(self) -> None:
        """Persisted seq should match device's current seq."""
        device = MagicMock()
        device.address = "DC:23:4D:21:43:A5"
        device.get_seq = MagicMock(return_value=7777)

        mock_store = MagicMock()
        mock_store.async_save = AsyncMock()

        coord = TuyaBLEMeshCoordinator(device)
        coord._seq_store = mock_store

        await coord._save_seq()

        mock_store.async_save.assert_called_once_with({"seq": 7777})
