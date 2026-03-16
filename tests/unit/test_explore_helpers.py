"""Unit tests for explore_device.py pure helper functions.

Tests classify_mesh_variant() and format_report() — pure functions
that take plain data, not bleak objects.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "custom_components" / "tuya_ble_mesh" / "lib"))

from explore_device import classify_mesh_variant, format_report
from tuya_ble_mesh.const import (
    SIG_MESH_PROVISIONING_SERVICE,
    SIG_MESH_PROXY_SERVICE,
    TELINK_CUSTOM_SERVICE,
    TUYA_CUSTOM_SERVICE,
    TUYA_MESH_SERVICE_UUID,
)


class TestClassifyMeshVariant:
    """Test mesh variant classification from service UUIDs."""

    def test_sig_mesh_provisioning(self) -> None:
        uuids = [TUYA_MESH_SERVICE_UUID, SIG_MESH_PROVISIONING_SERVICE]
        assert classify_mesh_variant(uuids) == "sig_mesh"

    def test_sig_mesh_proxy(self) -> None:
        uuids = [SIG_MESH_PROXY_SERVICE]
        assert classify_mesh_variant(uuids) == "sig_mesh"

    def test_tuya_proprietary(self) -> None:
        uuids = [TUYA_MESH_SERVICE_UUID, TUYA_CUSTOM_SERVICE]
        assert classify_mesh_variant(uuids) == "tuya_proprietary"

    def test_both_sig_and_tuya_prefers_sig(self) -> None:
        uuids = [SIG_MESH_PROVISIONING_SERVICE, TUYA_CUSTOM_SERVICE]
        assert classify_mesh_variant(uuids) == "sig_mesh"

    def test_unknown_no_mesh_services(self) -> None:
        uuids = [TUYA_MESH_SERVICE_UUID, "0000180a-0000-1000-8000-00805f9b34fb"]
        assert classify_mesh_variant(uuids) == "unknown"

    def test_unknown_empty_list(self) -> None:
        assert classify_mesh_variant([]) == "unknown"

    def test_sig_mesh_both_provisioning_and_proxy(self) -> None:
        uuids = [SIG_MESH_PROVISIONING_SERVICE, SIG_MESH_PROXY_SERVICE]
        assert classify_mesh_variant(uuids) == "sig_mesh"

    def test_telink_uuid_detected_as_tuya_proprietary(self) -> None:
        """Telink-based devices use a different UUID base but same suffixes."""
        uuids = [TELINK_CUSTOM_SERVICE]
        assert classify_mesh_variant(uuids) == "tuya_proprietary"

    def test_telink_uuid_with_sig_prefers_sig(self) -> None:
        uuids = [SIG_MESH_PROVISIONING_SERVICE, TELINK_CUSTOM_SERVICE]
        assert classify_mesh_variant(uuids) == "sig_mesh"


class TestFormatReport:
    """Test report formatting."""

    def _make_report(self, **overrides: object) -> str:
        """Build a report with sensible defaults, overridable per-test."""
        defaults: dict[str, object] = {
            "mac": "DC:23:4D:21:43:A5",
            "device_name": "out_of_mesh",
            "services": [],
            "device_info": {},
            "mesh_variant": "unknown",
            "readable_chars": [],
            "notifications": [],
            "scan_time": "2026-03-01T12:00:00",
        }
        defaults.update(overrides)
        return format_report(**defaults)  # type: ignore[arg-type]

    def test_contains_mac_address(self) -> None:
        report = self._make_report(mac="DC:23:4D:21:43:A5")
        assert "DC:23:4D:21:43:A5" in report

    def test_contains_device_name(self) -> None:
        report = self._make_report(device_name="out_of_mesh")
        assert "out_of_mesh" in report

    def test_contains_mesh_variant(self) -> None:
        report = self._make_report(mesh_variant="sig_mesh")
        assert "sig_mesh" in report

    def test_contains_scan_time(self) -> None:
        report = self._make_report(scan_time="2026-03-01T12:00:00")
        assert "2026-03-01T12:00:00" in report

    def test_services_section(self) -> None:
        services = [
            {
                "uuid": "00001827-0000-1000-8000-00805f9b34fb",
                "description": "Mesh Provisioning",
                "characteristics": [
                    {
                        "uuid": "00002adb-0000-1000-8000-00805f9b34fb",
                        "properties": ["write-without-response"],
                    },
                ],
            },
        ]
        report = self._make_report(services=services)
        assert "1 found" in report
        assert "00001827" in report
        assert "Mesh Provisioning" in report

    def test_device_info_section(self) -> None:
        info = {"Manufacturer": "Malmbergs", "Model Number": "9952126"}
        report = self._make_report(device_info=info)
        assert "Malmbergs" in report
        assert "9952126" in report

    def test_device_info_empty(self) -> None:
        report = self._make_report(device_info={})
        assert "not available" in report

    def test_readable_chars_section(self) -> None:
        chars = [
            {"uuid": "00002a29-0000-1000-8000-00805f9b34fb", "length": 10},
        ]
        report = self._make_report(readable_chars=chars)
        assert "1 read" in report
        assert "10 bytes" in report

    def test_notifications_section(self) -> None:
        notifs = [
            {
                "uuid": "00002adc-0000-1000-8000-00805f9b34fb",
                "timestamp": "12:00:00.123",
                "length": 20,
            },
        ]
        report = self._make_report(notifications=notifs)
        assert "1 received" in report
        assert "20 bytes" in report

    def test_no_notifications(self) -> None:
        report = self._make_report(notifications=[])
        assert "0 received" in report

    def test_report_is_multiline(self) -> None:
        report = self._make_report()
        assert "\n" in report
        lines = report.strip().split("\n")
        assert len(lines) > 10
