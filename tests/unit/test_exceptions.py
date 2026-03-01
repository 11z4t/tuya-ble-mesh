"""Unit tests for BLE exception hierarchy."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from tuya_ble_mesh.exceptions import (
    BLECharacteristicError,
    BLEConnectionError,
    BLEDeviceNotFoundError,
    BLEError,
    BLENotificationError,
    BLEServiceError,
    BLETimeoutError,
)

ALL_SUBCLASSES: list[type[BLEError]] = [
    BLEConnectionError,
    BLEDeviceNotFoundError,
    BLEServiceError,
    BLECharacteristicError,
    BLETimeoutError,
    BLENotificationError,
]


class TestInheritance:
    """All BLE exceptions inherit from BLEError."""

    def test_base_is_exception(self) -> None:
        assert issubclass(BLEError, Exception)

    @pytest.mark.parametrize("exc_class", ALL_SUBCLASSES)
    def test_subclass_inherits_from_ble_error(self, exc_class: type[BLEError]) -> None:
        assert issubclass(exc_class, BLEError)

    @pytest.mark.parametrize("exc_class", ALL_SUBCLASSES)
    def test_subclass_inherits_from_exception(self, exc_class: type[BLEError]) -> None:
        assert issubclass(exc_class, Exception)

    def test_subclass_count(self) -> None:
        """Exactly 6 direct subclasses of BLEError."""
        assert len(ALL_SUBCLASSES) == 6


class TestMessageFormatting:
    """Exceptions carry descriptive messages."""

    def test_base_message(self) -> None:
        exc = BLEError("generic BLE failure")
        assert str(exc) == "generic BLE failure"

    def test_connection_error_message(self) -> None:
        exc = BLEConnectionError("device DC:23:4D:21:43:A5 disconnected")
        assert "DC:23:4D:21:43:A5" in str(exc)
        assert "disconnected" in str(exc)

    def test_device_not_found_message(self) -> None:
        exc = BLEDeviceNotFoundError("no device found after 15s scan")
        assert "15s" in str(exc)

    def test_service_error_message(self) -> None:
        uuid = "00001827-0000-1000-8000-00805f9b34fb"
        exc = BLEServiceError(f"service {uuid} not found")
        assert uuid in str(exc)

    def test_characteristic_error_message(self) -> None:
        exc = BLECharacteristicError("write failed: not permitted")
        assert "write failed" in str(exc)

    def test_timeout_error_message(self) -> None:
        exc = BLETimeoutError("operation exceeded 10.0s limit")
        assert "10.0s" in str(exc)

    def test_notification_error_message(self) -> None:
        exc = BLENotificationError("subscribe failed for characteristic")
        assert "subscribe failed" in str(exc)


class TestCatchSemantics:
    """Verify that catch patterns work as expected."""

    def test_catch_base_catches_connection_error(self) -> None:
        with pytest.raises(BLEError):
            raise BLEConnectionError("connection lost")

    def test_catch_base_catches_device_not_found(self) -> None:
        with pytest.raises(BLEError):
            raise BLEDeviceNotFoundError("not found")

    def test_catch_base_catches_service_error(self) -> None:
        with pytest.raises(BLEError):
            raise BLEServiceError("no service")

    def test_catch_base_catches_timeout(self) -> None:
        with pytest.raises(BLEError):
            raise BLETimeoutError("timed out")

    def test_catch_specific_does_not_catch_sibling(self) -> None:
        """BLEConnectionError handler does not catch BLETimeoutError."""
        with pytest.raises(BLETimeoutError):
            try:
                raise BLETimeoutError("timed out")
            except BLEConnectionError:
                pytest.fail("Should not catch sibling exception")

    def test_notification_is_also_exception(self) -> None:
        """BLENotificationError is an instance of Exception."""
        exc = BLENotificationError("notify failed")
        assert isinstance(exc, Exception)

    @pytest.mark.parametrize("exc_class", ALL_SUBCLASSES)
    def test_isinstance_check(self, exc_class: type[BLEError]) -> None:
        exc = exc_class("test message")
        assert isinstance(exc, BLEError)
        assert isinstance(exc, Exception)
