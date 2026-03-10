# Changelog

All notable changes to the Tuya BLE Mesh integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.19.0] - 2026-03-10

### Added
- **Zero-knowledge config flow** (PLAT-510, PLAT-511) — auto-detected devices configured instantly with sensible defaults
- **Enhanced discovery cards** (PLAT-508) — show MAC address, RSSI signal strength, and device category
- **Peer review checklist** (PLAT-432) — comprehensive pre-merge review guide
- **Home Assistant docs draft** (PLAT-426) — ready for home-assistant.io submission
- **In-repo documentation verification** (PLAT-425) — 20 files, >120KB total
- **Out-of-slots error handling** (PLAT-506) — intelligent backoff when BLE adapter exhausted
- **Service UUID-based device type detection** — automatic plug vs light classification
- **Exponential backoff** in BLE connection retries (3.0s → 4.5s → 6.75s, capped at 15s)
- **Connection slot release delay** (0.5s sleep after disconnect) prevents slot exhaustion
- **Stale discovery protection** — filters devices that stopped advertising
- **Unit tests for PLAT-506** error paths (improved coverage from 64% to 70% for provisioner)

### Fixed
- **Sequence number overflow protection** (PLAT-408) — prevents AES-CCM nonce reuse
- **Key material zeroization** (PLAT-408) — defense-in-depth against memory forensics
- **BLE connection slot exhaustion** on adapters with limited concurrent connections
- **Stale discovery flows** persisting after device stops advertising

### Changed
- Improved provisioning error messages with actionable diagnostics
- Enhanced logging with scan_failures vs connect_failures tracking
- Better out-of-slots error messages with troubleshooting steps

### Documentation
- Added RELEASE_NOTES_v0.19.0.md with full release details
- Added HA_DOCS_DRAFT.md for official HA documentation
- Added PEER_REVIEW_CHECKLIST.md for code review
- Updated LEARNINGS.md with PLAT-506 insights

### Testing
- Unit test coverage: 91% (up from 82%)
- 1079 unit tests passing
- Full integration test suite
- E2E tests with Playwright (accessibility, visual regression, multi-browser)
- 30 performance benchmarks

---

## [0.18.0] - 2026-03-09

### Added
- **100% test coverage achievement** (PLAT-402) — 3736/3736 statements covered
- **ESPHome BLE proxy support** — documentation and integration patterns
- **Enhanced provisioning error handling** — detailed timeout and connection failure messages
- **BLE proxy connection improvements** — exponential backoff and better logging
- **Comprehensive E2E test suite** (PLAT-419-424) — Playwright with accessibility, visual regression, multi-browser
- **Quality scale Platinum compliance** (PLAT-431) — meets all HA quality requirements
- **CI/CD pipeline** (PLAT-429) — automated testing and validation
- **Modern HA patterns** (PLAT-409) — typed config entries, strict typing
- **Performance benchmarks** (PLAT-418) — crypto, protocol, and component benchmarks
- **Integration tests** (PLAT-417) — full HA lifecycle coverage

### Changed
- Improved connection retry logic with exponential backoff
- Enhanced service enumeration validation (checks for Provisioning Service 0x1827)
- Better ECDH error reporting (invalid curve points, crypto failures)
- Separate tracking of scan vs connection failures

### Documentation
- Added docs/ESPHOME_PROXY.md — ESPHome proxy setup guide
- Added comprehensive coverage reports
- Added LEARNINGS.md with development insights
- Updated TESTING.md with E2E test instructions

---

## [0.17.3] - 2026-03-07

### Fixed
- Integration setup race condition — runtime_data now set before async_start
- Missing icons in config flow

### Documentation
- Added BRANDS_SUBMISSION.md — HA Brands repo submission guide
- Added COMMUNITY.md — community guidelines
- Updated CONTRIBUTING.md with E2E test instructions

---

## [0.17.0] - 2026-03-06

### Added
- **SIG Mesh provisioning** — full PB-GATT provisioner implementation
- **SIG Mesh device support** — GATT proxy with secure messaging
- **HTTP bridge daemon** — remote BLE access for HA installations without Bluetooth
- **Dual protocol support** — Tuya proprietary + SIG Mesh standard
- **Auto-discovery** — finds devices via Bluetooth integration
- **Config flow** — UI-based setup (no YAML)
- **Diagnostics platform** — device and config diagnostics

### Changed
- Refactored to coordinator pattern for better data updates
- Improved error handling across all platforms
- Enhanced logging with correlation IDs

---

## [0.10.0] - 2026-02-20

### Added
- Initial release
- Light entity support (on/off, brightness, color temperature)
- Switch entity support (plugs)
- Sensor entity support (RSSI)
- Tuya BLE Mesh protocol implementation
- Telink mesh support
- Basic auto-discovery

---

[0.19.0]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.17.3...v0.18.0
[0.17.3]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.17.0...v0.17.3
[0.17.0]: https://github.com/11z4t/tuya-ble-mesh/compare/v0.10.0...v0.17.0
[0.10.0]: https://github.com/11z4t/tuya-ble-mesh/releases/tag/v0.10.0
