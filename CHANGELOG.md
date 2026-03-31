# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

---

## [0.38.0] — 2026-03-31

### Removed
- **`async_start()` deprecated method** (`coordinator.py`) — was swallowing connection
  exceptions, hiding failures from HA Core. Use `async_initial_connect()` which propagates
  exceptions so HA shows "Retrying setup" and handles backoff correctly.

### Fixed
- **CI pipeline** (`scripts/run-checks.sh`) — corrected tool paths (`ruff`/`bandit` in
  `~/.local/bin`), `detect-secrets` skips gracefully if not installed, `pip-audit` now
  scans only production requirements from `manifest.json` (not the test venv)
- **Crypto safety assert** (`crypto.py`) — added `# nosec B101` for intentional CTR-block
  guard that must survive optimised builds

---

## [0.37.0] — 2026-03-27

### Added
- **Dynamic manufacturer name** (`__init__.py`, `const.py`) — `KNOWN_VENDOR_IDS` dict maps
  vendor IDs to brand names (Malmbergs, AwoX, Dimond, …); `DeviceInfo.manufacturer` now
  shows the actual brand instead of a hardcoded fallback
- **Phase-specific provisioning errors** (`config_flow.py`, all 8 translations) — three new
  error keys (`provisioning_appkey_failed`, `provisioning_proxy_failed`,
  `provisioning_pbgatt_failed`) with actionable guidance for each failure phase
- **Connection quality dBm thresholds** (all 8 translations) — state labels for
  `good`/`marginal`/`poor` now include the dBm range so users understand the scale
- **Light brightness mode attributes** (`light.py`) — `extra_state_attributes` property
  exposes `brightness_mode` (`rgb`/`white`) and `device_brightness` for dashboards and
  automations
- **Bridge validation progress feedback** (`config_flow.py`) — `status` description
  placeholder shown during SIG/Telink bridge connection test so the UI isn't silent
- **Diagnostics response-time documentation** (`diagnostics.py`) — `_info` key in
  `response_times` explains the p50/p95/p99 percentile semantics

### Changed
- **RSSI sensor** (`sensor.py`) — `value_fn` returns `None` for RSSI = 0 (BLE Mesh
  placeholder) so the entity shows as unavailable rather than a misleading zero

### Fixed
- **Last-seen UTC docstring** (`sensor.py`) — clarifies that `_last_seen_datetime` returns
  UTC and that HA auto-converts to local time for display

---

## [0.36.4] — 2026-03-20

### Fixed
- **asyncio task GC** — four fire-and-forget tasks were silently dropped by the
  garbage collector before completing:
  - `transport/dispatcher.py`: `_send_with_retry` tasks (fixed in previous session)
  - `sig_mesh_provisioner_exchange.py`: `_process_notify` tasks created in `_on_notify()`
    callback — comment even said "prevent garbage collection" but the no-op lambda achieved
    nothing; replaced with `_notify_tasks: set` + add/discard pattern
  - `connection_manager.py`: reconnect-storm issue task and repair-issue task were local
    variables that went out of scope; added `_background_tasks: set` to `__init__` with
    proper lifecycle management
  - `coordinator.py`: `self.hass.async_create_task()` in `_on_vendor_update()` crashed
    with `AttributeError` in standalone/test mode (hass=None skips `super().__init__()`);
    replaced with `self._create_background_task()`
- **Rule S7 compliance** — `ValueError` in `power.py` replaced with `PowerControlError`;
  overly-broad `except Exception` in `sig_mesh_bridge.py` narrowed to specific types
- **Dead code removed** — `sig_mesh_bridge_telink.py`, `sig_mesh_bridge_http.py`,
  `logbook.py` dead branches, unused `type: ignore` comments
- **Translation** — `ble_adapter_busy` key added to issues namespace in all 9 language
  files; config flow type hints added for mypy --strict compliance
- **Coordinator refactor** — `_create_background_task()` helper extracted to unify
  HA-lifecycle-tracked vs standalone task creation across the coordinator

### Internal
- Full lib/ + HA integration code review completed (58 files)
- All 8 CI checks passing: ruff, mypy --strict, bandit, pip-audit, detect-secrets,
  pytest unit (1664 tests), pytest security (136 tests), pytest benchmark

---

## [0.36.3] — 2026-03-19

### Fixed
- **SIG plug pairing: "does not expose Provisioning Service (0x1827)"** — root cause was
  `use_services_cache=True` in `establish_connection` returning stale GATT services cached
  from when the device was in Proxy mode (0x1828) before factory-reset. Changed to
  `use_services_cache=False` so provisioning always does fresh service discovery and
  correctly sees the Provisioning Service (0x1827) after reset
- **Swedish (`sv`) translation placeholder validation errors** — 7 errors logged by HA on
  every startup:
  - `config.step.confirm.description`: removed unsupported `{category}` placeholder
  - `issues.bridge_unreachable.title`: removed `{host}:{port}` (placeholders not allowed in
    issue title); moved to description which now uses both `{host}` and `{port}`
  - `issues.connection_timeout.title`: removed `{device}` (not allowed in title); added
    `{device}` to description instead
  - `issues.reconnect_storm.title`: removed `{device}` (not allowed in title); description
    now includes both `{device}` and `{count}`

---

## [0.36.2] — 2026-03-19

### Fixed
- `TypeError: type 'ConfigEntry' is not subscriptable` on HA < 2024.4 — guard
  `ConfigEntry[TuyaBLEMeshRuntimeData]` under `TYPE_CHECKING` so older HA
  versions can load the integration without a startup crash

---

## [0.36.1] — 2026-03-19

### Fixed
- SIG Mesh plug provisioning timeout: `async_ble_device_from_address(connectable=True)`
  returned `None` for devices seen only via passive BLE scan — fallback to
  `connectable=False` added so bleak-retry-connector can still attempt the connection
- Provisioning outer timeout increased 20 s → 60 s to accommodate all 5 retry
  attempts with exponential backoff before being cancelled

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
