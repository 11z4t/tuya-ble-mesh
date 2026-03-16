"""Unit tests for DP mapping and YAML device profiles."""

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "custom_components" / "tuya_ble_mesh" / "lib"))

from tuya_ble_mesh.dps import (
    DataPointDef,
    DeviceProfile,
    TelinkCommandDef,
    list_profiles,
    load_profile,
    load_profile_by_model,
)
from tuya_ble_mesh.exceptions import ProtocolError

# Path to the real profiles directory
PROFILES_DIR = Path(__file__).resolve().parent.parent.parent / "profiles"


# --- DeviceProfile ---


class TestDeviceProfile:
    """Test DeviceProfile dataclass methods."""

    @pytest.fixture
    def profile(self) -> DeviceProfile:
        return DeviceProfile(
            name="Test Light",
            model="12345",
            category="dj",
            mesh_category=0x1012,
            capabilities=("power", "white_brightness"),
            data_points={
                1: DataPointDef(dp_id=1, name="power", dp_type="boolean"),
                3: DataPointDef(dp_id=3, name="brightness", dp_type="value"),
            },
            telink_commands={
                "power": TelinkCommandDef(
                    name="power",
                    opcode=0xD0,
                    params_on=b"\x01",
                    params_off=b"\x00",
                ),
            },
        )

    def test_has_capability_true(self, profile: DeviceProfile) -> None:
        assert profile.has_capability("power") is True

    def test_has_capability_false(self, profile: DeviceProfile) -> None:
        assert profile.has_capability("rgb") is False

    def test_get_dp_by_id(self, profile: DeviceProfile) -> None:
        dp = profile.get_dp(1)
        assert dp is not None
        assert dp.name == "power"

    def test_get_dp_missing(self, profile: DeviceProfile) -> None:
        assert profile.get_dp(99) is None

    def test_get_dp_by_name(self, profile: DeviceProfile) -> None:
        dp = profile.get_dp_by_name("brightness")
        assert dp is not None
        assert dp.dp_id == 3

    def test_get_dp_by_name_missing(self, profile: DeviceProfile) -> None:
        assert profile.get_dp_by_name("nonexistent") is None

    def test_get_command(self, profile: DeviceProfile) -> None:
        cmd = profile.get_command("power")
        assert cmd is not None
        assert cmd.opcode == 0xD0

    def test_get_command_missing(self, profile: DeviceProfile) -> None:
        assert profile.get_command("nonexistent") is None

    def test_frozen(self, profile: DeviceProfile) -> None:
        with pytest.raises(AttributeError):
            profile.name = "changed"  # type: ignore[misc]


# --- load_profile ---


class TestLoadProfile:
    """Test YAML profile loading."""

    def test_load_real_profile(self) -> None:
        path = PROFILES_DIR / "9952126_led_driver.yaml"
        profile = load_profile(path)
        assert profile.model == "9952126"
        assert profile.name == "Malmbergs LED Driver"
        assert profile.category == "dj"

    def test_capabilities(self) -> None:
        path = PROFILES_DIR / "9952126_led_driver.yaml"
        profile = load_profile(path)
        assert "power" in profile.capabilities
        assert "white_brightness" in profile.capabilities

    def test_data_points_loaded(self) -> None:
        path = PROFILES_DIR / "9952126_led_driver.yaml"
        profile = load_profile(path)
        assert 1 in profile.data_points
        assert profile.data_points[1].name == "power"
        assert profile.data_points[1].dp_type == "boolean"

    def test_data_point_range(self) -> None:
        path = PROFILES_DIR / "9952126_led_driver.yaml"
        profile = load_profile(path)
        dp3 = profile.data_points[3]
        assert dp3.value_range == (1, 127)

    def test_telink_commands_loaded(self) -> None:
        path = PROFILES_DIR / "9952126_led_driver.yaml"
        profile = load_profile(path)
        cmd = profile.telink_commands["power"]
        assert cmd.opcode == 0xD0
        assert cmd.params_on == b"\x01"
        assert cmd.params_off == b"\x00"

    def test_telink_command_range(self) -> None:
        path = PROFILES_DIR / "9952126_led_driver.yaml"
        profile = load_profile(path)
        cmd = profile.telink_commands["white_brightness"]
        assert cmd.param_range == (1, 127)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ProtocolError, match="not found"):
            load_profile(tmp_path / "nonexistent.yaml")

    def test_invalid_yaml_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("not a mapping", encoding="utf-8")
        with pytest.raises(ProtocolError, match="YAML mapping"):
            load_profile(path)

    def test_missing_required_key_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "incomplete.yaml"
        path.write_text("name: Test\ncategory: dj\n", encoding="utf-8")
        with pytest.raises(ProtocolError, match="model"):
            load_profile(path)

    def test_pyyaml_not_available_raises(self, tmp_path: Path) -> None:
        """Test that missing PyYAML dependency raises ProtocolError."""
        from unittest.mock import patch

        import tuya_ble_mesh.dps as dps_module

        path = tmp_path / "test.yaml"
        path.write_text("name: Test\nmodel: 123\ncategory: test\n", encoding="utf-8")

        with (
            patch.object(dps_module, "_YAML_AVAILABLE", False),
            pytest.raises(ProtocolError, match="PyYAML is required"),
        ):
            load_profile(path)

    def test_minimal_profile(self, tmp_path: Path) -> None:
        path = tmp_path / "minimal.yaml"
        path.write_text(
            textwrap.dedent("""\
                name: Minimal
                model: "999"
                category: test
            """),
            encoding="utf-8",
        )
        profile = load_profile(path)
        assert profile.model == "999"
        assert profile.capabilities == ()
        assert profile.data_points == {}
        assert profile.telink_commands == {}


# --- load_profile_by_model ---


class TestLoadProfileByModel:
    """Test profile lookup by model number."""

    def test_find_existing_model(self) -> None:
        profile = load_profile_by_model("9952126", PROFILES_DIR)
        assert profile is not None
        assert profile.model == "9952126"

    def test_missing_model_returns_none(self) -> None:
        profile = load_profile_by_model("0000000", PROFILES_DIR)
        assert profile is None

    def test_missing_directory_returns_none(self, tmp_path: Path) -> None:
        profile = load_profile_by_model("9952126", tmp_path / "nope")
        assert profile is None

    def test_skips_malformed_profiles(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("not a mapping", encoding="utf-8")
        good = tmp_path / "good.yaml"
        good.write_text(
            textwrap.dedent("""\
                name: Good
                model: "123"
                category: test
            """),
            encoding="utf-8",
        )
        profile = load_profile_by_model("123", tmp_path)
        assert profile is not None
        assert profile.model == "123"


# --- list_profiles ---


class TestListProfiles:
    """Test listing all profiles."""

    def test_list_real_profiles(self) -> None:
        profiles = list_profiles(PROFILES_DIR)
        assert len(profiles) >= 1
        models = [p.model for p in profiles]
        assert "9952126" in models

    def test_empty_directory(self, tmp_path: Path) -> None:
        assert list_profiles(tmp_path) == []

    def test_missing_directory(self, tmp_path: Path) -> None:
        assert list_profiles(tmp_path / "nope") == []

    def test_skips_malformed(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("just a string", encoding="utf-8")
        profiles = list_profiles(tmp_path)
        assert profiles == []


# --- DataPointDef ---


class TestDataPointDef:
    """Test DataPointDef dataclass."""

    def test_frozen(self) -> None:
        dp = DataPointDef(dp_id=1, name="power", dp_type="boolean")
        with pytest.raises(AttributeError):
            dp.name = "changed"  # type: ignore[misc]

    def test_default_description(self) -> None:
        dp = DataPointDef(dp_id=1, name="test", dp_type="value")
        assert dp.description == ""

    def test_default_range(self) -> None:
        dp = DataPointDef(dp_id=1, name="test", dp_type="value")
        assert dp.value_range is None


# --- TelinkCommandDef ---


class TestTelinkCommandDef:
    """Test TelinkCommandDef dataclass."""

    def test_frozen(self) -> None:
        cmd = TelinkCommandDef(name="power", opcode=0xD0)
        with pytest.raises(AttributeError):
            cmd.opcode = 0xFF  # type: ignore[misc]

    def test_defaults(self) -> None:
        cmd = TelinkCommandDef(name="test", opcode=0x00)
        assert cmd.params_on == b""
        assert cmd.params_off == b""
        assert cmd.param_range is None
