"""Shared fixtures and skip logic for hardware tests.

These tests require real BLE hardware and are NOT run in CI.
Run with: pytest tests/hardware/ -v -s
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

# Add lib/ to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

# Default target device
TARGET_MAC = "DC:23:4D:21:43:A5"
TARGET_MESH_NAME = b"out_of_mesh"
TARGET_MESH_PASSWORD = b"123456"  # pragma: allowlist secret

# Shelly Plug S for power cycling
SHELLY_HOST = "192.168.1.50"


def _check_bluetooth_available() -> bool:
    """Check if a Bluetooth adapter is available."""
    try:
        result = asyncio.get_event_loop().run_until_complete(
            asyncio.create_subprocess_exec(
                "hciconfig",
                "hci0",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        )
        stdout, _ = asyncio.get_event_loop().run_until_complete(result.communicate())
        return b"UP RUNNING" in stdout
    except Exception:
        return False


requires_bluetooth = pytest.mark.skipif(
    not _check_bluetooth_available(),
    reason="Bluetooth adapter (hci0) not available or not running",
)


@pytest.fixture
def target_mac() -> str:
    """Return the target device MAC address."""
    return TARGET_MAC


@pytest.fixture
def mesh_name() -> bytes:
    """Return the mesh network name."""
    return TARGET_MESH_NAME


@pytest.fixture
def mesh_password() -> bytes:  # pragma: allowlist secret
    """Return the mesh network password."""  # pragma: allowlist secret
    return TARGET_MESH_PASSWORD
