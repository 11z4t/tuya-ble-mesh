# Manual Testing Checklist

This checklist ensures thorough manual verification of the Tuya BLE Mesh integration before releases.

## Pre-Testing Setup

- [ ] Home Assistant version: ____________
- [ ] Integration version: ____________
- [ ] Test device(s): ____________
- [ ] Test environment: Dev / Staging / Production
- [ ] Tester name: ____________
- [ ] Date: ____________

---

## 1. Installation & Setup

### HACS Installation
- [ ] HACS shows integration in store
- [ ] Installation completes without errors
- [ ] Restart prompt appears
- [ ] Integration loads after restart

### Manual Installation
- [ ] Files copied to `custom_components/tuya_ble_mesh/`
- [ ] HA detects integration after restart
- [ ] No errors in logs during startup
- [ ] Integration appears in Integrations page

---

## 2. Configuration Flow

### Bluetooth Discovery
- [ ] BLE devices discovered automatically
- [ ] Device name displayed correctly
- [ ] MAC address shown (anonymized if needed)
- [ ] RSSI signal strength visible
- [ ] "Configure" button functional

### Manual Configuration
- [ ] "Add Integration" button works
- [ ] Search finds "Tuya BLE Mesh"
- [ ] Config dialog opens
- [ ] Required fields marked clearly
- [ ] Help text is helpful

### Field Validation
- [ ] Empty MAC address rejected
- [ ] Invalid MAC format rejected (e.g., "invalid")
- [ ] Empty mesh name rejected
- [ ] Empty mesh password rejected
- [ ] Valid inputs accepted

### Connection Testing
- [ ] "Test Connection" validates device reachability
- [ ] Error shown for unreachable devices
- [ ] Success message for reachable devices
- [ ] Timeout handles gracefully (30s max)

### Completion
- [ ] Integration appears in integrations list
- [ ] Device card shows correct info
- [ ] Entities created automatically
- [ ] No duplicate entries

---

## 3. Entity Functionality

### Light Entity
- [ ] Light appears in Lovelace
- [ ] Toggle on/off works
- [ ] Brightness slider works (0-100%)
- [ ] Color temperature slider works
- [ ] RGB color picker works
- [ ] State persists across HA restarts

### Switch Entity
- [ ] Switch appears in UI
- [ ] Toggle on/off works
- [ ] State updates within 5s
- [ ] Icon changes with state

### Sensor Entities
- [ ] RSSI sensor shows signal strength
- [ ] Battery sensor shows percentage (if supported)
- [ ] Firmware version displayed
- [ ] Sensors update regularly

### Diagnostics
- [ ] Diagnostic entities hidden by default
- [ ] Can be enabled from entity settings
- [ ] Show useful debug info (MAC, firmware, etc.)

---

## 4. Device Management

### Device Info
- [ ] Device card shows manufacturer
- [ ] Model displayed correctly
- [ ] Firmware version shown
- [ ] MAC address visible
- [ ] Serial number (if available)

### Device Actions
- [ ] "Configure" opens options dialog
- [ ] "Delete" prompts confirmation
- [ ] Delete removes all entities
- [ ] Re-adding device works after delete

### Multiple Devices
- [ ] Can add multiple Tuya devices
- [ ] Each device independent
- [ ] No conflicts between devices
- [ ] State updates don't interfere

---

## 5. Service Calls

### turn_on
```yaml
service: light.turn_on
target:
  entity_id: light.tuya_mesh_light
data:
  brightness: 128
  color_temp: 300
```
- [ ] Service executes without error
- [ ] Light turns on
- [ ] Brightness applied
- [ ] Color temp applied

### turn_off
```yaml
service: light.turn_off
target:
  entity_id: light.tuya_mesh_light
```
- [ ] Service executes
- [ ] Light turns off
- [ ] State updates in UI

---

## 6. Error Handling

### Connection Failures
- [ ] Device offline shows "unavailable"
- [ ] Error logged appropriately
- [ ] Retry attempts visible in logs
- [ ] Eventually recovers when device online

### Invalid Commands
- [ ] Brightness > 255 handled gracefully
- [ ] Invalid color temp rejected
- [ ] Error messages clear and helpful

### Bluetooth Issues
- [ ] BLE adapter offline detected
- [ ] Integration doesn't crash HA
- [ ] Repair flow triggered
- [ ] Recovery when adapter restored

---

## 7. Performance

### Response Time
- [ ] Light on/off < 2s
- [ ] Brightness change < 2s
- [ ] Color change < 2s
- [ ] State refresh < 5s

### Resource Usage
- [ ] CPU usage normal (<5% idle)
- [ ] Memory usage stable
- [ ] No memory leaks over 24h
- [ ] BLE scanning doesn't saturate CPU

### Concurrency
- [ ] Multiple commands queued properly
- [ ] No race conditions
- [ ] State consistency maintained

---

## 8. Accessibility (WCAG 2.1 AA)

### Keyboard Navigation
- [ ] Tab through config flow fields
- [ ] Enter submits forms
- [ ] Esc cancels dialogs
- [ ] Focus indicators visible

### Screen Reader
- [ ] Form labels announced
- [ ] Error messages read aloud
- [ ] State changes announced
- [ ] Help text accessible

### Visual
- [ ] Color contrast sufficient (4.5:1 text)
- [ ] Focus indicators clear
- [ ] No color-only indicators
- [ ] Text scalable to 200%

---

## 9. Multi-Browser Testing

### Desktop
- [ ] Chrome/Chromium (latest)
- [ ] Firefox (latest)
- [ ] Safari (latest, macOS)
- [ ] Edge (latest)

### Mobile
- [ ] Mobile Chrome (Android)
- [ ] Mobile Safari (iOS)
- [ ] Responsive layout works
- [ ] Touch targets adequate (44x44px)

---

## 10. Upgrade & Migration

### Upgrade from Previous Version
- [ ] Config entries preserved
- [ ] Entities retain unique_id
- [ ] State history continuous
- [ ] No orphaned entities

### Breaking Changes
- [ ] Migration path documented
- [ ] Users notified in release notes
- [ ] Old configs handled gracefully

---

## 11. Security

### Authentication
- [ ] Mesh password required
- [ ] Password stored securely (encrypted)
- [ ] No plaintext passwords in logs

### Communication
- [ ] BLE traffic encrypted
- [ ] Sequence numbers prevent replay
- [ ] MITM protection active

### Logs
- [ ] No sensitive data in logs
- [ ] Debug mode safe to enable
- [ ] Secrets masked in traces

---

## 12. Documentation

### README
- [ ] Installation steps accurate
- [ ] Configuration examples work
- [ ] Troubleshooting section helpful
- [ ] Device compatibility list updated

### In-App Help
- [ ] Config flow has help text
- [ ] Error messages actionable
- [ ] Links to docs functional

---

## 13. Edge Cases

### Unusual Configurations
- [ ] 0 devices configured
- [ ] 10+ devices configured
- [ ] Device with long name (>50 chars)
- [ ] Special characters in mesh name

### Network Issues
- [ ] BLE interference handling
- [ ] Out-of-range device handling
- [ ] Rapid on/off commands
- [ ] Command during disconnect

---

## 14. Regression Testing

Run this checklist before **every release** to catch regressions.

### Critical Paths (Must Pass)
- [ ] Add device via discovery
- [ ] Turn light on/off
- [ ] Adjust brightness
- [ ] Device shows unavailable when offline
- [ ] Config entry can be deleted

### Known Issues
Document any known issues discovered:

1. ____________________________________________
2. ____________________________________________
3. ____________________________________________

---

## 15. Release Readiness

Before marking release ready:
- [ ] All critical tests pass
- [ ] No P0/P1 bugs open
- [ ] Documentation updated
- [ ] CHANGELOG.md updated
- [ ] Version bumped correctly
- [ ] Git tag created

---

## Sign-Off

**Tester Signature:** ________________________

**Date:** ____________

**Approved for Release:** ☐ Yes  ☐ No  ☐ Conditional

**Notes:**
____________________________________________
____________________________________________
____________________________________________

---

## Automated Test Coverage

This manual checklist supplements automated tests:
- **Unit tests:** Protocol, crypto, device logic
- **Integration tests:** Config flow, entity setup
- **Security tests:** Replay attacks, fuzzing
- **E2E tests:** UI interaction, accessibility
- **Visual regression:** Screenshot comparison
- **Multi-browser:** Playwright cross-browser

**Automation Coverage:** ~85%

**Manual Testing Focus:** UX, edge cases, real hardware
