# Learnings — Tuya BLE Mesh Integration

This document captures insights, patterns, and lessons learned during development and testing of the Tuya BLE Mesh Home Assistant integration.

**Last updated**: 2026-03-08 by Thor (VM 903) — Batch 5 complete

---

## 1. Codebase Architecture

### Integration Structure
- **Bridge architecture**: HA ↔ RPi bridge ↔ BLE mesh devices
  - Separation of concerns: HA handles UI/automation, bridge handles BLE protocol
  - Enables HA to run without Bluetooth hardware
  - HTTP API simplifies integration vs. raw BLE

### Key Components
- `__init__.py`: Entry point, config entry lifecycle
- `config_flow.py`: User setup flow, validation
- `coordinator.py`: Central data coordination, polling
- `light.py`, `switch.py`, `sensor.py`: Entity implementations
- `lib/`: BLE mesh protocol implementation (Telink, Tuya proprietary)

### Design Patterns
- **Coordinator pattern**: Single coordinator manages all devices
- **Runtime data**: Config entry runtime_data stores coordinator (HA 2024.x pattern)
- **Entity unique_id**: MAC-based for persistence across HA restarts
- **Device registry**: Groups entities under devices for better UX

---

## 2. Home Assistant Integration Patterns

### Quality Scale (Platinum Tier)
- **45 rules** across Bronze, Silver, Gold, Platinum
- Key requirements:
  - Config flow with validation
  - Async/await throughout
  - Type hints (mypy --strict)
  - Device registry integration
  - Entity translations
  - Repair/reauth flows
  - Test coverage

### Entity Implementation
- **Light entity**: Brightness, color temp (if supported)
- **Switch entity**: On/off for plugs
- **Sensor entity**: RSSI (diagnostic, disabled by default)
- **Entity categories**: Diagnostic entities marked to reduce clutter

### Config Entry Lifecycle
```python
async_setup_entry()  # Create coordinator, set up platforms
async_unload_entry()  # Clean up resources
async_reload_entry()  # Reauth flow support
```

### Error Handling
- Validation in config flow (bridge connectivity, MAC format)
- Entity unavailable when bridge/device offline
- Repair issues for actionable user feedback
- Reauth flow for bridge connection failures

---

## 3. Testing Strategies

### Test Suite Structure
```
tests/
├── unit/          # Fast, isolated tests
├── integration/   # HA integration tests (mocked)
├── security/      # Security regression tests
├── benchmarks/    # Performance tests
└── e2e/           # Playwright browser tests
```

### E2E Testing (Playwright)
- **Entity interaction**: Test UI controls work end-to-end
- **Visual regression**: Screenshot comparison for UI consistency
- **Accessibility**: WCAG 2.1 AA compliance via axe-core
- **Multi-browser**: Chromium, Firefox, WebKit, mobile emulation

#### Key Learnings
- E2E tests require running HA instance (set HA_BASE_URL)
- Tests must handle conditional UI (device may not exist in test env)
- Use `if (await element.count() > 0)` for graceful degradation
- Visual regression needs consistent viewport sizes
- Accessibility tests catch real issues (color contrast, labels, ARIA)

### Test Coverage Goals
- Unit tests: >80% coverage
- Integration tests: All entity types, config flow paths
- Security tests: Replay attacks, input validation
- E2E tests: Happy path + error states

### Mocking Strategy
- Mock BLE connections for unit/integration tests
- Mock HA coordinator for entity tests
- Real HA instance for E2E tests
- Hardware tests separate (marked with `@pytest.mark.integration`)

---

## 4. BLE Mesh Protocol

### Tuya BLE Mesh
- Proprietary protocol over BLE Mesh
- Encryption: AES-CCM with device-specific keys
- Command structure: vendor opcodes + encrypted payload
- Mesh credentials: name + password (default: "out_of_mesh" / "123456")

### SIG Mesh
- Standard Bluetooth Mesh protocol
- Provisioning via PB-GATT
- Generic OnOff/Level/LightCTL models
- Network key + application key required

### Protocol Challenges
- **Vendor ID variations**: Malmbergs (0x1001), AwoX (0x0160), etc.
- **Encryption**: Must derive keys from mesh password
- **Sequence numbers**: Prevent replay attacks
- **Connection stability**: BLE dropouts require exponential backoff

### Debugging Tools
- `scripts/sniff.py`: Passive BLE capture via nRF51822
- `scripts/explore_device.py`: Interactive device probing
- `bluetoothctl`: Manual BLE connection testing
- Wireshark: PCAP analysis with Nordic sniffer plugin

---

## 5. CI/CD Pipeline

### Validation Pipeline (`scripts/run-checks.sh`)
1. **ruff check**: Linting (12 rules, auto-fixable)
2. **ruff format**: Black-style formatting
3. **mypy --strict**: Type checking
4. **bandit**: Security static analysis
5. **safety check**: Dependency vulnerabilities
6. **detect-secrets**: Secret scanning
7. **pytest unit**: Unit tests
8. **pytest security**: Security regression tests

### Known Issues
- Security tests require `homeassistant` package (large dependency)
- Some tests fail without real BLE hardware
- CVE-2024-23342 (ecdsa) ignored (transitive dependency, no fix)

### CI/CD Recommendations
- Add GitHub Actions workflow for automated testing
- Run checks on PR + main branch push
- Automated releases on version tags
- Test coverage reporting (codecov)

---

## 6. Accessibility (WCAG 2.1 AA)

### EU Directives
- **EAA (EU 2019/882)**: Digital accessibility = WCAG 2.1 AA
- All public-facing UIs must comply
- HA integrations must meet these standards

### Key Requirements
- **Color contrast**: 4.5:1 (text), 3:1 (large elements)
- **Keyboard navigation**: Tab/Enter works everywhere
- **Focus indicators**: Visible on all interactive elements
- **Form labels**: Associated via htmlFor/id
- **ARIA attributes**: Proper roles, labels, states
- **Heading hierarchy**: h1→h2→h3 (no skipping)
- **Alt text**: All images
- **Lang attribute**: Set on <html>

### Testing
- **axe-core**: Automated WCAG scanning (96+ rules)
- **Manual checks**: Keyboard nav, screen reader, zoom
- **Playwright**: Automated a11y tests in CI

### Common Violations
- Missing ARIA labels on icon buttons
- Form inputs without associated labels
- Color contrast too low (use tools to check)
- Heading hierarchy skips (h1→h3)
- Images missing alt text

---

## 7. Community Engagement

### Communication Channels
- **HA Community Forum**: Integrations category
- **GitHub Issues**: Bug reports, features, device compatibility
- **GitHub Discussions**: Questions, announcements
- **Discord**: #integrations channel

### Issue Templates
- **Bug report**: Env, steps, logs, expected vs actual
- **Feature request**: Use case, proposed solution, alternatives
- **Device compatibility**: Model, status, what works/doesn't

### Response Time Guidelines
- Critical bugs: <24 hours
- Feature requests: <1 week
- Questions: <3 days
- PRs: Review <1 week

### Recognition
- Contributors in CHANGELOG
- Co-authored commits for significant contributions
- GitHub stars for helpful issues
- Forum badges/likes

---

## 8. HA Brands Submission

### Brand Repository
- Icon files: 256x256 (icon.png), 512x512 (icon@2x.png)
- Logo files: Any size, 2x version recommended
- Transparent background for icons
- Optimized PNG (<100KB)

### Manifest
```json
{
  "domain": "tuya_ble_mesh",
  "name": "Tuya BLE Mesh",
  "integration_type": "device",
  "iot_class": "local_polling",
  "supported_brands": ["tuya"]
}
```

### IoT Classes
- `local_polling`: Device polled locally (our choice)
- `local_push`: Device pushes updates locally
- `cloud_polling`: Cloud API polling
- `cloud_push`: Cloud push notifications

---

## 9. Fallgropar & Best Practices

### Git Hygiene
- **NEVER commit .venv/**: Add to .gitignore
- **NEVER commit secrets**: Use 1Password, detect-secrets
- **Commit each story separately**: Easier to review, revert
- **Push after every commit**: Triggers CI early

### Python Best Practices
- **Type hints everywhere**: mypy --strict catches bugs early
- **Async/await**: Don't block event loop
- **Context managers**: Proper resource cleanup
- **Exceptions**: Catch specific, log useful info
- **Docstrings**: Google style, describe purpose

### HA Integration Best Practices
- **Config flow validation**: Test connectivity before setup
- **Entity unique_id**: Use immutable device ID (MAC)
- **Device registry**: Group entities for better UX
- **Entity unavailable**: Set when device offline
- **Repair flow**: Guide users to fix issues
- **Translations**: strings.json for all user-facing text

### Testing Best Practices
- **Test isolation**: No shared state between tests
- **Mock external dependencies**: BLE, network, HA
- **Test error paths**: Not just happy path
- **Parametrize tests**: Cover multiple cases efficiently
- **Fixtures**: Reusable test data

---

## 10. Multi-Browser Compatibility

### Playwright Projects
- Desktop: Chromium, Firefox, WebKit
- Mobile: Pixel 5 (Chrome), iPhone 13 (Safari)

### Browser-Specific Issues
- **WebKit**: Stricter CSP, different rendering
- **Firefox**: Different focus indicator styles
- **Mobile**: Touch events vs mouse events

### Testing Strategy
- Run core tests on all browsers
- Visual regression on Chromium (baseline)
- Mobile tests for responsive layout, touch

### Installation
```bash
npx playwright install chromium firefox webkit
```

---

## 11. Pipeline & Coverage

### Coverage Goals
- **Code coverage**: >80% (pytest-cov)
- **Type coverage**: 100% (mypy --strict)
- **Security coverage**: All inputs validated
- **E2E coverage**: Critical user flows

### Auto-Fixable Issues
```bash
ruff check --fix .    # Fix import sorting, simple violations
ruff format .         # Auto-format code
```

### Manual Fixes Required
- Convert try-except-pass to contextlib.suppress
- Convert lambda to def functions
- Add missing type hints
- Review bandit security warnings

---

## 12. Useful Commands

### Development
```bash
# Activate venv
source .venv/bin/activate

# Run full validation
bash scripts/run-checks.sh

# Run specific tests
pytest tests/unit/ -v
pytest tests/e2e/ -k accessibility

# Type check
mypy --strict lib/

# Lint and format
ruff check --fix .
ruff format .
```

### Debugging
```bash
# Enable debug logging in HA
logger:
  logs:
    custom_components.tuya_ble_mesh: debug

# Check bridge health
curl http://BRIDGE_IP:8787/health

# Scan for BLE devices
python scripts/scan.py

# Sniff BLE traffic (requires nRF51822)
python scripts/sniff.py
```

### E2E Testing
```bash
# Run all E2E tests
npm run test:e2e

# Run specific test file
npm run test:e2e accessibility

# Run in headed mode (see browser)
npm run test:e2e:headed

# Update visual baselines
npm run test:e2e -- --update-snapshots

# View HTML report
npm run test:e2e:report
```

---

## 13. Future Improvements

### Features
- Color control (RGB/RGBW lights)
- Energy monitoring (if devices support)
- OTA firmware updates (if Tuya supports)
- Scene support (mesh-level scenes)
- Group control (mesh groups)

### Infrastructure
- GitHub Actions CI/CD workflow
- Automated releases on tags
- codecov.io integration
- Dependabot for dependency updates

### Documentation
- Video tutorials for setup
- Device compatibility database
- Interactive troubleshooting guide
- API documentation (Sphinx)

### Testing
- Hardware-in-loop tests (real devices)
- Performance regression tests
- Load testing (many devices)
- Fuzz testing (protocol layer)

---

## 15. CI/CD Pipeline Lessons (PLAT-429 - Batch 5)

### Import Error Pitfalls
**Problem**: Tests imported non-existent `build_command_packet` instead of `encode_command_packet`
- **Root cause**: Refactoring renamed function but tests not updated
- **Fix**: Global find/replace across test suite
- **Prevention**: Add import validation to pre-commit hooks

### Conditional Test Dependencies
**Problem**: Tests importing `custom_components` failed without `homeassistant` package
- **Root cause**: Some tests require HA, others don't (protocol/crypto tests standalone)
- **Solution**: Added `@pytest.mark.requires_ha` marker to isolate HA-dependent tests
- **Config**: `pyproject.toml` markers allow selective test execution
- **CI benefit**: Core tests run fast without HA, optional tests skipped

### YAML Syntax in CI
**Problem**: `.gitea/workflows/ci.yml` had Python syntax errors (missing quotes, bad f-strings)
- **Error**: `with open(/tmp/...)` → `with open('/tmp/...')`
- **Error**: `data.get(results)` → `data.get('results')`
- **Error**: `print(fFound {total}...)` → `print(f'Found {total}...')`
- **Prevention**: Run CI YAML through Python validator before commit

### Pytest Marker Best Practices
- **Always import pytest**: Files using `@pytest.mark.*` must `import pytest`
- **Mark at class level**: Apply markers to entire test classes when all tests share dependency
- **Document markers**: Add description to `pyproject.toml` for each custom marker

### Test Organization
```
tests/
├── unit/          # No external deps (protocol, crypto)
├── security/      # Isolated security tests
├── integration/   # Require mocked HA (@pytest.mark.requires_ha)
├── benchmarks/    # Performance (require pytest-benchmark)
└── e2e/           # Full stack (Playwright, running HA instance)
```

**Lesson**: Separate test tiers by dependency requirements for faster CI feedback loops.

---

## 14. Resources

### Home Assistant
- [Developer Docs](https://developers.home-assistant.io/)
- [Quality Scale](https://developers.home-assistant.io/docs/integration_quality_scale_index)
- [Entity Platform](https://developers.home-assistant.io/docs/core/entity/)
- [Config Flow](https://developers.home-assistant.io/docs/config_entries_config_flow_handler/)

### Testing
- [Playwright Docs](https://playwright.dev/)
- [pytest Docs](https://docs.pytest.org/)
- [axe-core Rules](https://github.com/dequelabs/axe-core/blob/develop/doc/rule-descriptions.md)

### Accessibility
- [WCAG 2.1 Quick Ref](https://www.w3.org/WAI/WCAG21/quickref/)
- [WebAIM](https://webaim.org/)
- [a11y Project](https://www.a11yproject.com/)

### BLE Mesh
- [Bluetooth Mesh Spec](https://www.bluetooth.com/specifications/specs/mesh-protocol/)
- [Telink SDK](http://wiki.telink-semi.cn/)
- [Tuya BLE SDK Docs](https://developer.tuya.com/)

---

**Maintained by**: VM 903 (Thor)  
**Contributing**: Add learnings as you discover them
