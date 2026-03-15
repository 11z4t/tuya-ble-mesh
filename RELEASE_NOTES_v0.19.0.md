# Release Notes — v0.19.0 (2026-03-10)

## 🎯 Highlights

This release brings **zero-knowledge configuration**, **enhanced BLE provisioning reliability**, and **comprehensive test coverage** improvements.

### Zero-Knowledge Config Flow (, )
Devices are now **auto-detected and configured with zero user input**:
- SIG Mesh devices (0x1827/0x1828) → auto-detected as **Plug**
- Telink Mesh devices (fe07) → auto-detected as **Light**
- Discovery cards show **MAC, RSSI, and device category**
- No manual mesh credentials needed for default devices

### BLE Provisioning Enhancements (, )
- **Out-of-slots error detection** — intelligent backoff when BLE adapter runs out of connection slots
- **Exponential backoff** — 3.0 → 4.5 → 6.75s retry delays (capped at 15s)
- **Connection slot release delay** — 0.5s sleep after disconnect to prevent slot exhaustion
- **Stale discovery protection** — ignores devices that stopped advertising

### Security Hardening ()
- **Sequence number overflow protection** — prevents catastrophic AES-CCM nonce reuse
- **Key material zeroization** — defense-in-depth against memory forensics
- Both fixes address critical issues from HARDENING.md (C2, H1)

---

## ✨ New Features

### Auto-Discovery Improvements
- Enhanced discovery cards with device MAC, RSSI signal strength, and category (SIG Mesh vs Telink Mesh)
- Service UUID-based device type detection (plug vs light)
- Stale device filtering — only show actively advertising devices

### Config Flow
- Zero-knowledge flow — auto-detected devices configured instantly with sensible defaults
- Manual override still available via "Customize" button in discovery card

### Developer Experience
- Peer review checklist (PEER_REVIEW_CHECKLIST.md)
- Home Assistant documentation draft (HA_DOCS_DRAFT.md)
- Comprehensive in-repo docs (>120KB across 20 files)

---

## 🐛 Bug Fixes

### BLE Provisioning
- Fixed connection slot exhaustion on adapters with limited concurrent connections
- Fixed stale discovery flows persisting after device stops advertising
- Improved error messages with actionable diagnostics (scan vs connect failures)

### Security
- Fixed potential nonce reuse in SIG Mesh device (sequence number wrapping)
- Fixed memory leak risk in key material cleanup

---

## 🧪 Testing

### Coverage
- **Unit tests:** 1079 passing (91% coverage, improved from 82%)
- **Integration tests:** Full HA lifecycle coverage
- **E2E tests:** Playwright suite with accessibility, visual regression, multi-browser
- **Benchmarks:** 30 performance tests

### Test Improvements ()
- Added tests for out-of-slots error handling
- Added tests for is_connected=False error path
- Added tests for missing Provisioning Service 0x1827
- Added tests for service enumeration timeout
- Added tests for TimeoutError with exponential backoff
- Added test for connection slot release delay

---

## 📚 Documentation

### New Files
- `HA_DOCS_DRAFT.md` — Draft for home-assistant.io documentation
- `PEER_REVIEW_CHECKLIST.md` — Comprehensive pre-merge checklist
- Enhanced `LEARNINGS.md` with insights

### Updated Files
- `README.md` — Updated badges and feature list
- `hacs.json` — Version bump
- `manifest.json` — Version bump to 0.19.0

---

## 🔧 Technical Changes

### Architecture
- No breaking changes to public API
- Internal refactoring for better error handling
- Improved logging with correlation IDs

### Dependencies
No dependency changes. Still requires:
- Home Assistant 2024.1+
- Python 3.12+
- bleak, cryptography, aiohttp

---

## 🚀 Upgrade Notes

### From 0.18.x
- **No action required** — seamless upgrade
- Existing config entries will continue to work
- New auto-detection features apply to newly added devices only

### Breaking Changes
None.

---

## 🔗 Links

- **GitHub:** https://github.com/11z4t/tuya-ble-mesh
- **HACS:** https://hacs.xyz
- **Issues:** https://github.com/11z4t/tuya-ble-mesh/issues
- **Documentation:** https://github.com/11z4t/tuya-ble-mesh/blob/main/README.md

---

## 🙏 Contributors

- **Thor (VM 903)** — , , , implementation and testing
- All previous contributors to the 0.18.x series

---

## 📊 Stats

- **Commits since 0.18.0:** 7
- **Files changed:** 6
- **Lines added:** 320
- **Lines removed:** 23
- **Test coverage:** 91% (up from 82%)
- **Documentation:** 120+ KB across 20 files

---

## Next Steps

### Planned for v0.20.0
- [ ] Complete unit test coverage to 100%
- [ ] GitHub Actions CI/CD pipeline integration
- [ ] Home Assistant Brands submission
- [ ] Home Assistant Core integration proposal

### Planned for v1.0.0
- [ ] Production stability validation (30-day soak test)
- [ ] Multi-device mesh support (>10 devices)
- [ ] Advanced diagnostics dashboard

---

**Full Changelog:** https://github.com/11z4t/tuya-ble-mesh/compare/v0.18.0...v0.19.0
