"""Data Point (DP) mapping and YAML device profile loading.

Provides DeviceProfile for loading device capabilities from YAML
profiles (Rule S8), and DataPointMap for DP ID to capability mapping.

New device types are added by creating a YAML file in profiles/,
not by modifying Python code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tuya_ble_mesh.exceptions import ProtocolError

try:
    import yaml

    _YAML_AVAILABLE = True
except ImportError:  # pragma: no cover
    _YAML_AVAILABLE = False

_LOGGER = logging.getLogger(__name__)

# Default profiles directory (relative to project root)
_DEFAULT_PROFILES_DIR = Path(__file__).resolve().parent.parent.parent / "profiles"


@dataclass(frozen=True)
class DataPointDef:
    """Definition of a single data point from a device profile."""

    dp_id: int
    name: str
    dp_type: str
    description: str = ""
    value_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class TelinkCommandDef:
    """Definition of a Telink command from a device profile."""

    name: str
    opcode: int
    params_on: bytes = b""
    params_off: bytes = b""
    param_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class DeviceProfile:
    """Device profile loaded from a YAML file.

    Describes the capabilities, data points, and Telink commands
    for a specific device model.
    """

    name: str
    model: str
    category: str
    mesh_category: int
    capabilities: tuple[str, ...]
    data_points: dict[int, DataPointDef] = field(default_factory=dict)
    telink_commands: dict[str, TelinkCommandDef] = field(default_factory=dict)

    def has_capability(self, capability: str) -> bool:
        """Check if the device supports a capability."""
        return capability in self.capabilities

    def get_dp(self, dp_id: int) -> DataPointDef | None:
        """Look up a data point definition by ID."""
        return self.data_points.get(dp_id)

    def get_dp_by_name(self, name: str) -> DataPointDef | None:
        """Look up a data point definition by name."""
        for dp in self.data_points.values():
            if dp.name == name:
                return dp
        return None

    def get_command(self, name: str) -> TelinkCommandDef | None:
        """Look up a Telink command definition by name."""
        return self.telink_commands.get(name)


def _parse_data_points(raw: dict[str, Any]) -> dict[int, DataPointDef]:
    """Parse data_points section from YAML."""
    result: dict[int, DataPointDef] = {}
    for dp_id_str, dp_data in raw.items():
        dp_id = int(dp_id_str)
        value_range = None
        if "range" in dp_data:
            r = dp_data["range"]
            value_range = (int(r[0]), int(r[1]))
        result[dp_id] = DataPointDef(
            dp_id=dp_id,
            name=str(dp_data.get("name", "")),
            dp_type=str(dp_data.get("type", "raw")),
            description=str(dp_data.get("description", "")),
            value_range=value_range,
        )
    return result


def _parse_telink_commands(raw: dict[str, Any]) -> dict[str, TelinkCommandDef]:
    """Parse telink_commands section from YAML."""
    result: dict[str, TelinkCommandDef] = {}
    for cmd_name, cmd_data in raw.items():
        param_range = None
        if "param_range" in cmd_data:
            r = cmd_data["param_range"]
            param_range = (int(r[0]), int(r[1]))
        result[cmd_name] = TelinkCommandDef(
            name=cmd_name,
            opcode=int(cmd_data["opcode"]),
            params_on=bytes(cmd_data.get("params_on", [])),
            params_off=bytes(cmd_data.get("params_off", [])),
            param_range=param_range,
        )
    return result


def load_profile(path: Path) -> DeviceProfile:
    """Load a device profile from a YAML file.

    Args:
        path: Path to the YAML profile file.

    Returns:
        Parsed DeviceProfile.

    Raises:
        ProtocolError: If the file is missing, malformed, or PyYAML
            is not installed.
    """
    if not _YAML_AVAILABLE:
        msg = "PyYAML is required for device profiles (pip install pyyaml)"
        raise ProtocolError(msg)

    if not path.is_file():
        msg = f"Profile not found: {path}"
        raise ProtocolError(msg)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"Profile must be a YAML mapping: {path}"
        raise ProtocolError(msg)

    required = ("name", "model", "category")
    for key in required:
        if key not in raw:
            msg = f"Profile missing required key '{key}': {path}"
            raise ProtocolError(msg)

    data_points = _parse_data_points(raw.get("data_points", {}))
    telink_commands = _parse_telink_commands(raw.get("telink_commands", {}))

    return DeviceProfile(
        name=str(raw["name"]),
        model=str(raw["model"]),
        category=str(raw["category"]),
        mesh_category=int(raw.get("mesh_category", 0)),
        capabilities=tuple(str(c) for c in raw.get("capabilities", [])),
        data_points=data_points,
        telink_commands=telink_commands,
    )


def load_profile_by_model(
    model: str,
    profiles_dir: Path | None = None,
) -> DeviceProfile | None:
    """Find and load a device profile by model number.

    Searches all YAML files in the profiles directory for a matching
    model number.

    Args:
        model: Model number to search for (e.g. ``9952126``).
        profiles_dir: Directory to search. Defaults to ``profiles/``.

    Returns:
        The matching DeviceProfile, or None if not found.
    """
    search_dir = profiles_dir or _DEFAULT_PROFILES_DIR
    if not search_dir.is_dir():
        _LOGGER.warning("Profiles directory not found: %s", search_dir)
        return None

    for yaml_file in sorted(search_dir.glob("*.yaml")):
        try:
            profile = load_profile(yaml_file)
            if profile.model == model:
                return profile
        except ProtocolError:
            _LOGGER.warning("Skipping malformed profile: %s", yaml_file)
            continue

    return None


def list_profiles(
    profiles_dir: Path | None = None,
) -> list[DeviceProfile]:
    """Load all device profiles from the profiles directory.

    Args:
        profiles_dir: Directory to search. Defaults to ``profiles/``.

    Returns:
        List of successfully loaded profiles.
    """
    search_dir = profiles_dir or _DEFAULT_PROFILES_DIR
    if not search_dir.is_dir():
        return []

    result: list[DeviceProfile] = []
    for yaml_file in sorted(search_dir.glob("*.yaml")):
        try:
            result.append(load_profile(yaml_file))
        except ProtocolError:
            _LOGGER.warning("Skipping malformed profile: %s", yaml_file)
            continue

    return result
