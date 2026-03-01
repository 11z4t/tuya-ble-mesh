"""Unit tests for the Malmbergs BT exception hierarchy."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "lib"))

from tuya_ble_mesh.exceptions import (
    AuthenticationError,
    BLEConnectionError,
    BLEDeviceNotFoundError,
    BLEError,
    BLETimeoutError,
    ConnectionError,
    CryptoError,
    DeviceNotFoundError,
    MalformedPacketError,
    MalmbergsBTError,
    PowerControlError,
    ProtocolError,
    ProvisioningError,
    SecretAccessError,
    TimeoutError,
)

# --- Inheritance tests ---


class TestInheritance:
    """All exceptions inherit from MalmbergsBTError and Exception."""

    def test_base_is_exception(self) -> None:
        assert issubclass(MalmbergsBTError, Exception)

    @pytest.mark.parametrize(
        "exc_cls",
        [
            ConnectionError,
            DeviceNotFoundError,
            TimeoutError,
            ProvisioningError,
            ProtocolError,
            CryptoError,
            SecretAccessError,
            PowerControlError,
        ],
    )
    def test_direct_subclass_inherits_from_base(self, exc_cls: type) -> None:
        assert issubclass(exc_cls, MalmbergsBTError)

    def test_malformed_packet_inherits_from_protocol_error(self) -> None:
        assert issubclass(MalformedPacketError, ProtocolError)
        assert issubclass(MalformedPacketError, MalmbergsBTError)

    def test_authentication_inherits_from_crypto_error(self) -> None:
        assert issubclass(AuthenticationError, CryptoError)
        assert issubclass(AuthenticationError, MalmbergsBTError)

    def test_subclass_count(self) -> None:
        direct = [
            cls
            for cls in MalmbergsBTError.__subclasses__()
            if cls.__module__ == "tuya_ble_mesh.exceptions"
        ]
        assert len(direct) == 8


# --- Message formatting ---


class TestMessageFormatting:
    """Exception messages carry through to str()."""

    @pytest.mark.parametrize(
        ("exc_cls", "msg"),
        [
            (MalmbergsBTError, "base error"),
            (ConnectionError, "connection lost"),
            (DeviceNotFoundError, "DC:23:4D:21:43:A5 not found"),
            (TimeoutError, "scan timed out after 15s"),
            (ProvisioningError, "handshake step 2 failed"),
            (ProtocolError, "unexpected opcode 0xFF"),
            (MalformedPacketError, "packet too short: 3 bytes"),
            (CryptoError, "decryption failed"),
            (AuthenticationError, "pair proof mismatch"),
            (SecretAccessError, "vault unreachable"),
            (PowerControlError, "Shelly HTTP 500"),
        ],
    )
    def test_message(self, exc_cls: type, msg: str) -> None:
        exc = exc_cls(msg)
        assert str(exc) == msg


# --- Catch semantics ---


class TestCatchSemantics:
    """Verify that except clauses work as expected."""

    def test_catch_base_catches_all(self) -> None:
        for exc_cls in [
            ConnectionError,
            DeviceNotFoundError,
            TimeoutError,
            ProvisioningError,
            ProtocolError,
            MalformedPacketError,
            CryptoError,
            AuthenticationError,
            SecretAccessError,
            PowerControlError,
        ]:
            with pytest.raises(MalmbergsBTError):
                raise exc_cls("test")

    def test_catch_protocol_catches_malformed(self) -> None:
        with pytest.raises(ProtocolError):
            raise MalformedPacketError("bad packet")

    def test_catch_crypto_catches_auth(self) -> None:
        with pytest.raises(CryptoError):
            raise AuthenticationError("bad key")

    def test_specific_does_not_catch_sibling(self) -> None:
        with pytest.raises(DeviceNotFoundError):
            try:
                raise DeviceNotFoundError("not found")
            except ConnectionError:
                pytest.fail("Sibling exception should not be caught")

    @pytest.mark.parametrize(
        "exc_cls",
        [
            ConnectionError,
            DeviceNotFoundError,
            TimeoutError,
            ProvisioningError,
            ProtocolError,
            MalformedPacketError,
            CryptoError,
            AuthenticationError,
            SecretAccessError,
            PowerControlError,
        ],
    )
    def test_isinstance_check(self, exc_cls: type) -> None:
        exc = exc_cls("test")
        assert isinstance(exc, MalmbergsBTError)
        assert isinstance(exc, Exception)


# --- Backward compatibility aliases ---


class TestBackwardCompatibility:
    """Phase 1 scripts use BLE* names which are now aliases."""

    def test_ble_error_is_malmbergs_bt_error(self) -> None:
        assert BLEError is MalmbergsBTError

    def test_ble_connection_error_is_connection_error(self) -> None:
        assert BLEConnectionError is ConnectionError

    def test_ble_device_not_found_is_device_not_found(self) -> None:
        assert BLEDeviceNotFoundError is DeviceNotFoundError

    def test_ble_timeout_is_timeout(self) -> None:
        assert BLETimeoutError is TimeoutError

    def test_catch_alias_catches_new_name(self) -> None:
        with pytest.raises(BLEError):
            raise ConnectionError("test")

    def test_catch_new_name_catches_alias_raise(self) -> None:
        with pytest.raises(MalmbergsBTError):
            raise BLEDeviceNotFoundError("test")
