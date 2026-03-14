# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- Replace raw `BleakScanner` with HA's `async_ble_device_from_address()` for RSSI polling (H3)
- Add input validation for mesh credential length (≤16 bytes) and Vendor ID format
- Rename `ConnectionError` to `MeshConnectionError` to avoid shadowing Python built-in (M7)
- Add `done_callback` on transition tasks to surface exceptions (M6)
- Enforce TimeoutError → use Python 3.11+ built-in instead of `asyncio.TimeoutError`

### Added
- Complete Swedish (`sv.json`) translation: all config steps, errors, abort, entity, exceptions, services, issues
- New error keys: `invalid_credential_length`, `invalid_vendor_id`, `invalid_bridge_host`
- Backward-compatible alias `ConnectionError = MeshConnectionError` for existing code
- Duplicate MAC address detection in config flow (prevents adding the same device twice)
- `get_diagnostics` service now returns the diagnostics dict directly for use in scripts and automations

### Changed
- Swedish `sig_plug` step description updated to reflect auto-provisioning (removed stale `/tmp/mesh_keys.json` reference)
- Config flow `confirm` step now shows device name, MAC, signal strength in rich format
- `get_diagnostics` service: renamed `rssi` field to `rssi_dbm` for clarity

### Fixed
- Code quality: import sort, context manager collapsing, unused import removal
- Swedish translation `reauth_confirm` step added (was missing)
- Swedish translation `abort.reauth_successful` added (was missing)
- Test suite: fixed 42 failing tests across bridge, connection, provisioner, device, and integration layers
- Bridge command tests: patched both HTTP methods correctly (POST for submit, GET for poll)
- Async coroutine tests: `_handle_segment` and `_dispatch_access_payload` now properly awaited in tests
- Connection retry logic: broadened exception catch to handle all provisioning failure modes
- Provisioner cleanup delay reduced from 1.0 s to 0.5 s (aligns with spec recommendation)

---

## [0.17.3] — 2026-03-09

### Added
- Enhanced discovery cards showing MAC address, RSSI, and device category (PLAT-508)
- Zero-knowledge config flow for auto-detected devices (no manual key entry)
- HACS metadata and integration icons (icon.png, icon.svg, icon@2x.png)

### Fixed
- Discovery flow stale device handling — auto-detects device type from advertisement
- BLE provisioning connection slot exhaustion (PLAT-506)
- Integration setup race condition in async_setup_entry

### Changed
- Config flow discovery now uses `async_bluetooth_device_from_address` to avoid unnecessary scans

---

## [0.17.2] — 2026-03-08

### Added
- 100% unit test coverage across all modules (PLAT-402)
- Comprehensive integration tests including production lifecycle scenarios
- Security test suite (bandit, detect-secrets)
- E2E tests (Playwright) for UI flows
- Accessibility tests (axe-core, WCAG 2.1 AA)
- BLE proxy support for ESPHome proxy provisioning
- RSSI adaptive polling: adjusts interval based on signal stability
- Sequence number persistence across HA restarts (SIG Mesh)

### Fixed
- Config flow coverage increased from 82% to 100%
- Coordinator coverage edge cases for reconnect backoff
- Missing icons for light entity (icon.json)

### Security
- CRLF injection prevention in bridge host validation
- `writer.wait_closed()` added after all socket operations
- SIG Mesh sequence number 24-bit overflow check
- `_abort_if_unique_id_configured()` in Bluetooth discovery step

---

## [0.17.1] — 2026-03-05

### Added
- SIG Mesh auto-provisioning via PB-GATT (Mesh Profile Section 5.4)
- ECDH (FIPS P-256) key exchange for zero-knowledge provisioning
- DevKey derivation from provisioning exchange
- Full provisioning exchange: Invite → Capabilities → Start → PublicKey → Confirmation → Random → Data → Complete
- SIG Mesh segmentation/reassembly for large messages
- Proxy PDU parsing for SIG Mesh over GATT
- Composition data parsing (Page 0)
- Config AppKey add and Model App Bind
- SIG Mesh devices: light and plug support

### Changed
- Config flow: separate steps for Telink bridge, SIG bridge, and SIG plug
- Coordinator: handles both `MeshDevice` (Telink) and `SIGMeshDevice` (SIG Mesh)

---

## [0.17.0] — 2026-02-28

### Added
- Initial release on Gitea (private) and GitHub (11z4t/tuya-ble-mesh)
- Telink BLE Mesh protocol support (proprietary Tuya BLE Mesh)
- Bridge architecture: HA → HTTP → RPi bridge → BLE Mesh
- Auto-discovery via BLE advertisement scanning
- Reauth flow for credential updates
- Repair issues for bridge connectivity problems
- RSSI sensor entity
- Firmware version sensor entity
- Power and energy sensor entities (via Shelly integration)
- Keep-alive with exponential backoff reconnection
- Command queue with TTL for reliable delivery
- SIG Mesh bridge protocol over TCP
- Profile-based DPS (Data Point Specification) from YAML files
- Diagnostic info in `diagnostics.py`
- Platinum HA Quality Scale compliance

### Supported Devices
- **Malmbergs BT Smart** LED Driver (9952126) — brightness, on/off
- **Malmbergs BT Smart** Smart Plug S17 — on/off, SIG Mesh

[Unreleased]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.17.3...HEAD
[0.17.3]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.17.2...v0.17.3
[0.17.2]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.17.1...v0.17.2
[0.17.1]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.17.0...v0.17.1
[0.17.0]: https://github.com/11z4t/tuya-ble-mesh/releases/tag/v0.17.0
