# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

---

## [0.36.0] — 2026-03-19

### Fixed
- **Critical:** Telink LED driver pairing completely broken — `TELINK_CHAR_NOTIFY`
  does not exist; replaced with correct constant `TELINK_CHAR_STATUS`
  (`00010203-0405-0607-0809-0a0b0c0d1911`). All Telink pairing threw `ImportError`
  before the handshake even started (root cause of TBM-PAIRING-DEBUG)
- Missing error/abort translation keys in all 9 translation files (`en`, `sv`, `da`,
  `de`, `fi`, `fr`, `kl`, `nb`, `nl`, `uk`): `cannot_connect_ble`, `pairing_failed`,
  `verify_failed`, `device_type_mismatch`, `unknown_device_type`, `ble_adapter_busy`,
  `invalid_bridge_host` (error), `reconfigure_successful`, `not_in_pairing_mode`,
  `entry_not_found` (abort) — users no longer see blank errors during pairing

### Changed
- Full linting, type checking, and security pipeline green (ruff, mypy --strict,
  bandit, pip-audit, detect-secrets — 8/8 checks passing)

### Tests
- 1800 tests passing (unit + security)

---

## [0.35.0] — 2026-03-16

### Added
- Device triggers for Tuya BLE Mesh automations
- Logbook integration for state change events
- BLE adapter busy (0x0a) workaround via `HaBleakClientWrapper`
- `ConfigEntryNotReady` on initial connection failure (proper HA lifecycle)
- Background task tracking in coordinator for clean shutdown

### Changed
- Config flow split into modular files (<300 lines each)
- `lib/` deduplicated — single source of truth under `custom_components/`
- Routine INFO logging reduced to DEBUG (less logspam)
- Light entity migrated to `CoordinatorEntity`
- `__getattr__/__setattr__` proxy removed from coordinator
- `config_flow VERSION=1` added; reconnect debounce delay added

### Fixed
- `BluetoothServiceInfoBleak` NameError on startup
- Duplicate repair translation keys
- Staleness detection for push-only coordinator
- Regression guard against `sys.path` manipulation

### Tests
- 1922 tests passing (unit + integration + security)

---

## [0.34.1] — 2026-03-16

### Fixed
- Remove `manufacturer_id 1447` BLE matcher (too broad, matched all Tuya BLE devices)

---

## [0.34.0] — 2026-03-16

### Changed
- Quality Scale review updates; removed `quality_scale: platinum` from manifest
- Connection quality extracted to shared helpers module

---

## [0.33.1] — 2026-03-16

### Added
- HA Bluetooth API integration — uses `async_ble_device_from_address` instead of raw `BleakScanner`
- S17 SIG Mesh plugs accepted without UUID check in discovery

---

## [0.33.0] — 2026-03-16

### Added
- Migrated BLE layer to HA Bluetooth API

---

## [0.32.0] — 2026-03-16

### Added
- RSSI populated from HA BLE connection for all device types

### Fixed
- Discovery card now shows device type clearly
- SIG Mesh plug re-discovery after removal
- Telink pairing — use user-configured credentials, not hardcoded defaults
- SIG Mesh provisioning incomplete — added `POST_COMPLETE_DELAY`

---

## [0.31.0] — 2026-03-16

### Fixed
- Telink pairing — enable BLE notifications before reading pair response
- `ConnectionManager` extracted from `coordinator.py`

---

## [0.30.x] — 2026-03-15

### Added
- Device type factory pattern in `__init__.py`
- `ErrorClassifier` module for connection error categorization
- Auto-detect device type from BLE advertisement (skips dropdown)

### Fixed
- BLE callback using non-existent `is_connected` — switched to `state.available`
- Hidden internal BLE name `out_of_mesh` from discovery card

---

## [0.29.x] — 2026-03-15

### Added
- `MeshDeviceProtocol(Protocol)` interface — all device classes implement it
- `BridgeCommandError` / `BridgeUnreachableError` replacing legacy Shelly error classes
- `print()` → `_LOGGER` migration complete — no print calls in `lib/` or `custom_components/`

---

## [0.28.x] — 2026-03-14

### Added
- SIG Mesh GATT proxy connection over TCP bridge

### Fixed
- S17 SIG Mesh plug setup crash on config entry load

---

## [0.27.x] — 2026-03-13

### Added
- Input validation for mesh credential length (≤16 bytes) and Vendor ID format
- Duplicate MAC address detection in config flow
- `get_diagnostics` service returns dict directly for use in scripts

### Changed
- Renamed `ConnectionError` → `MeshConnectionError` (avoids shadowing Python built-in)

---

## [0.26.x] — 2026-03-12

### Added
- Complete Swedish (`sv.json`) translation
- Config flow `confirm` step shows device name, MAC, signal strength
- `reauth_confirm` step added

### Fixed
- `reauth_successful` abort message missing from Swedish translation

---

## [0.17.3] — 2026-03-09

### Added
- Enhanced discovery cards showing MAC address, RSSI, and device category
- Zero-knowledge config flow for auto-detected devices (no manual key entry)
- HACS metadata and integration icons (icon.png, icon.svg, icon@2x.png)

### Fixed
- Discovery flow stale device handling — auto-detects device type from advertisement
- BLE provisioning connection slot exhaustion
- Integration setup race condition in `async_setup_entry`

---

## [0.17.2] — 2026-03-08

### Added
- 100% unit test coverage across all modules
- Comprehensive integration tests including production lifecycle scenarios
- Security test suite (bandit, detect-secrets)
- BLE proxy support for ESPHome proxy provisioning
- RSSI adaptive polling — adjusts interval based on signal stability
- Sequence number persistence across HA restarts (SIG Mesh)

### Security
- CRLF injection prevention in bridge host validation
- `writer.wait_closed()` after all socket operations
- SIG Mesh sequence number 24-bit overflow check

---

## [0.17.1] — 2026-03-05

### Added
- SIG Mesh auto-provisioning via PB-GATT (Mesh Profile Section 5.4)
- ECDH (FIPS P-256) key exchange for zero-knowledge provisioning
- Full provisioning exchange: Invite → Capabilities → Start → PublicKey → Confirmation → Random → Data → Complete
- SIG Mesh segmentation/reassembly for large messages
- SIG Mesh devices: light and plug support

---

## [0.17.0] — 2026-02-28

### Added
- Initial release on Gitea (private) and GitHub (`11z4t/tuya-ble-mesh`)
- Telink BLE Mesh protocol support (proprietary Tuya BLE Mesh)
- Bridge architecture: HA → HTTP → RPi bridge → BLE Mesh
- Auto-discovery via BLE advertisement scanning
- Reauth flow for credential updates
- Repair issues for bridge connectivity problems
- RSSI sensor entity and firmware version sensor
- Keep-alive with exponential backoff reconnection
- Command queue with TTL for reliable delivery
- Profile-based DPS from YAML files
- Diagnostic info in `diagnostics.py`

### Supported Devices
- **Malmbergs BT Smart** LED Driver (9952126) — brightness, on/off
- **Malmbergs BT Smart** Smart Plug S17 — on/off, SIG Mesh

[Unreleased]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.35.0...HEAD
[0.35.0]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.34.1...v0.35.0
[0.34.1]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.34.0...v0.34.1
[0.34.0]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.33.1...v0.34.0
[0.33.1]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.33.0...v0.33.1
[0.33.0]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.32.0...v0.33.0
[0.32.0]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.31.0...v0.32.0
[0.31.0]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.30.7...v0.31.0
[0.17.3]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.17.2...v0.17.3
[0.17.2]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.17.1...v0.17.2
[0.17.1]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.17.0...v0.17.1
[0.17.0]: https://github.com/11z4t/tuya-ble-mesh/releases/tag/v0.17.0
