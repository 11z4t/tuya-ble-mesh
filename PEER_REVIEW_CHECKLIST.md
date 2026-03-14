# Peer Review Checklist — Tuya BLE Mesh Integration

**Version:** 0.19.0 (pre-release)
**Review Date:** 2026-03-10
**Reviewer:** _________

---

## Pre-Review Verification

- [ ] All tests pass: `pytest tests/unit/ tests/integration/ -v`
- [ ] Linting passes: `ruff check .`
- [ ] Formatting passes: `ruff format --check .`
- [ ] Type checking passes: `mypy --strict custom_components/ lib/`
- [ ] Security scan passes: `bandit -r custom_components/ lib/`
- [ ] No secrets in code: `detect-secrets scan --baseline .secrets.baseline`
- [ ] Coverage report: `pytest --cov --cov-report=html` (target: >90%)

---

## Code Quality

### Architecture
- [ ] Clear separation between `lib/tuya_ble_mesh` (protocol) and `custom_components/tuya_ble_mesh` (HA integration)
- [ ] No HA dependencies in `lib/` (library must be standalone)
- [ ] Coordinator pattern used correctly for data updates
- [ ] Config flow follows HA best practices (async, typed, validated)

### Type Safety
- [ ] All functions have type hints (arguments and return types)
- [ ] `mypy --strict` compliance (no type: ignore without justification)
- [ ] TypedDict / dataclass used for structured data
- [ ] ConfigEntry typed via TypeAlias pattern

### Error Handling
- [ ] All external calls (BLE, HTTP, network) wrapped in try/except
- [ ] Exceptions logged with context (address, operation, correlation_id)
- [ ] User-facing errors provide actionable guidance
- [ ] No swallowed exceptions (empty except blocks)

### Security
- [ ] No secrets hardcoded (keys, passwords, tokens)
- [ ] CRLF injection protection in user inputs
- [ ] Command injection protection (no shell=True, validated paths)
- [ ] Key material never logged or printed
- [ ] ECDH keys generated securely (cryptography library)
- [ ] Nonce reuse protection (sequence number overflow check)

### Performance
- [ ] No blocking I/O in async functions
- [ ] BLE operations run in executor (non-blocking)
- [ ] Exponential backoff on retries (not linear)
- [ ] Connection pooling / reuse where applicable
- [ ] Memory leaks checked (no circular references without weakref)

---

## Integration-Specific

### Config Flow
- [ ] Auto-discovery works (Bluetooth integration)
- [ ] Manual entry accepts valid MAC addresses
- [ ] Invalid inputs show clear error messages
- [ ] Unique ID set correctly (prevents duplicates)
- [ ] Options flow allows reconfiguration

### Entities
- [ ] Light entity supports: on/off, brightness, color temperature
- [ ] Switch entity supports: on/off (plugs only)
- [ ] Sensor entity shows RSSI
- [ ] Entities show as "unavailable" when disconnected
- [ ] Entity naming follows HA conventions

### Coordinator
- [ ] Polling interval appropriate (30s default)
- [ ] Updates batched (not per-entity)
- [ ] Connection state tracked correctly
- [ ] Reconnect logic handles transient failures
- [ ] No infinite retry loops

### Diagnostics
- [ ] Device diagnostics include: firmware, RSSI, connection state
- [ ] Config diagnostics redact sensitive data (keys, passwords)
- [ ] JSON serialization works for all diagnostic data

---

## Testing

### Unit Tests
- [ ] All critical paths covered (>90% coverage)
- [ ] Error paths tested (exceptions, timeouts, invalid data)
- [ ] Mocks used correctly (AsyncMock for async functions)
- [ ] No real BLE/HTTP calls in unit tests
- [ ] Tests are deterministic (no random failures)

### Integration Tests
- [ ] Full lifecycle tested (setup → update → unload)
- [ ] Config flow tested with mocked HA
- [ ] Entity state updates verified
- [ ] Error recovery tested (connection loss, invalid responses)

### E2E Tests (Playwright)
- [ ] Config flow UI tested in browser
- [ ] Entity interaction tested (toggle, brightness slider)
- [ ] Visual regression checked (screenshots)
- [ ] Accessibility checked (WCAG 2.1 AA)
- [ ] Multi-browser tested (Chromium, Firefox, WebKit)

### Hardware Tests (if available)
- [ ] Tested with real Malmbergs BT Smart devices
- [ ] Provisioning verified (SIG Mesh PB-GATT)
- [ ] Control verified (on/off, brightness, color temp)
- [ ] Reconnect verified (power cycle device)

---

## Documentation

### In-Repo Docs
- [ ] README.md: clear installation and setup instructions
- [ ] CONTRIBUTING.md: development workflow documented
- [ ] ARCHITECTURE.md: design decisions explained
- [ ] TESTING.md: test procedures documented
- [ ] SECURITY.md: security policy and contact info
- [ ] LEARNINGS.md: insights and lessons captured

### Code Documentation
- [ ] All modules have docstrings
- [ ] All classes have docstrings
- [ ] All public functions have docstrings (Google style)
- [ ] Complex logic has inline comments
- [ ] Type hints supplement (not replace) docstrings

### User-Facing Docs
- [ ] HA_DOCS_DRAFT.md ready for home-assistant.io submission
- [ ] BRANDS_SUBMISSION.md ready for HA Brands repo
- [ ] hacs.json configured correctly

---

## Compliance

### Home Assistant Requirements
- [ ] manifest.json: all required fields present
- [ ] DOMAIN matches manifest.json and directory name
- [ ] quality_scale.yaml: all declared features implemented
- [ ] No deprecated HA APIs used (2024.1+ compatibility)
- [ ] Config flow uses async methods (no sync blocking)

### HACS Requirements
- [ ] hacs.json present and valid
- [ ] Brand files: icon.png, logo.png (correct sizes)
- [ ] README badges: HACS, HA version, license
- [ ] Releases follow semantic versioning

### Licensing
- [ ] MIT license file present
- [ ] All dependencies have compatible licenses
- [ ] No GPL code included (HA is Apache 2.0)

---

## Pre-Release Checklist

- [ ] Version number updated in manifest.json
- [ ] CHANGELOG.md updated with all changes
- [ ] All TODOs resolved or documented
- [ ] All FIXME comments addressed
- [ ] No debug prints or console.log statements
- [ ] No commented-out code blocks
- [ ] Git tags created for release
- [ ] GitHub release notes drafted

---

## Critical Issues Found

_List any blocking issues that must be resolved before merge/release:_

1.
2.
3.

---

## Minor Issues / Suggestions

_List non-blocking issues or improvement suggestions:_

1.
2.
3.

---

## Reviewer Sign-Off

**Reviewer Name:** _________
**Date:** _________
**Recommendation:** [ ] Approve  [ ] Request Changes  [ ] Reject

**Comments:**

