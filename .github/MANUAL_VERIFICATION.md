# Manual Verification Checklist

This checklist should be completed before each release to ensure the integration works correctly with real Home Assistant and real hardware.

## Prerequisites

- [ ] Home Assistant 2024.1+ running
- [ ] At least one Tuya BLE Mesh device available
- [ ] Raspberry Pi with Bluetooth running bridge daemon
- [ ] Integration installed (HACS or manual)

---

## 1. Installation & Setup

### 1.1 Integration Installation
- [ ] Install via HACS without errors
- [ ] Manual installation works (copy to custom_components/)
- [ ] HA restart completes successfully
- [ ] No errors in HA logs after restart

### 1.2 Bridge Daemon
- [ ] Bridge daemon starts without errors
- [ ] Bridge responds to HTTP health check
- [ ] Bridge can scan for BLE devices
- [ ] Bridge logs show BLE adapter ready

---

## 2. Config Flow

### 2.1 Add Integration
- [ ] **Settings → Devices & Services → Add Integration** shows "Tuya BLE Mesh"
- [ ] Config flow opens without errors
- [ ] All fields render correctly

### 2.2 Form Validation
- [ ] Invalid MAC address shows error
- [ ] Invalid IP address shows error
- [ ] Invalid port shows error
- [ ] Empty required fields show error

### 2.3 Bridge Connection
- [ ] Bridge reachability check works
- [ ] Connection error shows helpful message
- [ ] Successful connection proceeds to device setup

### 2.4 Device Addition
- [ ] Light device type creates light entity
- [ ] Plug device type creates switch entity
- [ ] MAC address is validated
- [ ] Mesh credentials are accepted

---

## 3. Entity Functionality

### 3.1 Light Entity
- [ ] Light entity appears in UI
- [ ] Entity name is correct
- [ ] Icon is appropriate
- [ ] State shows on/off correctly

#### Light Controls
- [ ] Turn on works
- [ ] Turn off works
- [ ] Brightness slider works (1-100%)
- [ ] Color temperature slider works (if supported)
- [ ] State updates in UI after command

#### Light Attributes
- [ ] `friendly_name` is set
- [ ] `supported_features` lists brightness/color_temp
- [ ] `brightness` attribute updates
- [ ] `color_temp` attribute updates (if supported)

### 3.2 Switch Entity (Plugs)
- [ ] Switch entity appears
- [ ] Turn on works
- [ ] Turn off works
- [ ] State reflects actual device state

### 3.3 Sensor Entity (RSSI)
- [ ] RSSI sensor appears
- [ ] Value is a number (dBm)
- [ ] Value updates periodically
- [ ] Unit of measurement is `dBm`

---

## 4. Device Page

### 4.1 Device Info
- [ ] Device appears in **Devices & Services**
- [ ] Device name is correct
- [ ] Device model is shown
- [ ] Manufacturer is "Tuya"
- [ ] All entities listed under device

### 4.2 Device Diagnostics
- [ ] **Download Diagnostics** button works
- [ ] Diagnostics JSON is valid
- [ ] Diagnostics contain:
  - MAC address
  - Bridge URL
  - Connection state
  - Entity states
  - No sensitive data (passwords redacted)

---

## 5. Automation & Scenes

### 5.1 Automations
- [ ] Can create automation with light trigger
- [ ] Can create automation with light action
- [ ] Automation executes correctly
- [ ] State changes trigger automations

### 5.2 Scenes
- [ ] Can add light to scene
- [ ] Scene saves light state
- [ ] Scene restores light state correctly

---

## 6. Performance & Reliability

### 6.1 Response Time
- [ ] Commands execute within 1 second
- [ ] No noticeable lag in UI updates
- [ ] Rapid commands don't cause errors

### 6.2 Connection Stability
- [ ] Device stays connected for >1 hour
- [ ] Auto-reconnect works after BLE dropout
- [ ] No memory leaks over 24 hours

### 6.3 Error Handling
- [ ] Bridge offline shows appropriate error
- [ ] Device unreachable shows "unavailable"
- [ ] Invalid commands show error in logs
- [ ] Recovery after errors works

---

## 7. Accessibility (WCAG 2.1 AA)

### 7.1 Keyboard Navigation
- [ ] Can navigate config flow with Tab
- [ ] Can submit form with Enter
- [ ] Focus indicators are visible
- [ ] No keyboard traps

### 7.2 Screen Reader
- [ ] Form labels are announced
- [ ] Errors are announced
- [ ] State changes are announced
- [ ] ARIA labels are present

### 7.3 Visual
- [ ] Color contrast meets 4.5:1 (text)
- [ ] Focus indicators meet 3:1
- [ ] UI works at 200% zoom
- [ ] No information by color alone

---

## 8. Multi-Browser Testing

Test in:
- [ ] Chrome/Chromium (desktop)
- [ ] Firefox (desktop)
- [ ] Safari (desktop or iOS)
- [ ] Mobile Chrome (phone/tablet)
- [ ] Mobile Safari (iPhone/iPad)

### Per Browser:
- [ ] Config flow works
- [ ] Entities render correctly
- [ ] Controls are interactive
- [ ] No JavaScript errors in console

---

## 9. Edge Cases

### 9.1 Multiple Devices
- [ ] Can add 2+ devices
- [ ] Each device gets unique entity_id
- [ ] Devices don't interfere with each other
- [ ] All devices controllable simultaneously

### 9.2 HA Restart
- [ ] Entities restore state after HA restart
- [ ] Devices reconnect automatically
- [ ] No errors in logs after restart

### 9.3 Bridge Restart
- [ ] Integration handles bridge restart gracefully
- [ ] Auto-reconnect works
- [ ] State becomes "unavailable" then recovers

### 9.4 Device Power Cycle
- [ ] Device reconnects after power cycle
- [ ] State updates correctly
- [ ] No duplicate entities created

---

## 10. Documentation

### 10.1 User Documentation
- [ ] README is accurate
- [ ] Installation steps work
- [ ] Configuration examples are correct
- [ ] Troubleshooting section helps

### 10.2 Developer Documentation
- [ ] Code comments are helpful
- [ ] Docstrings are present
- [ ] Architecture is documented
- [ ] Contributing guide is clear

---

## 11. Logs & Debugging

### 11.1 Log Levels
- [ ] INFO logs are concise
- [ ] DEBUG logs are detailed
- [ ] No ERROR logs during normal operation
- [ ] Warnings are actionable

### 11.2 Error Messages
- [ ] User-facing errors are clear
- [ ] Technical details in debug logs
- [ ] Stack traces are helpful
- [ ] No sensitive data in logs

---

## Sign-Off

**Tested by**: _______________  
**Date**: _______________  
**HA Version**: _______________  
**Integration Version**: _______________  
**Device Models Tested**: _______________

**Overall Status**: ☐ Pass  ☐ Pass with minor issues  ☐ Fail

**Notes**:
```
[Add any additional observations or issues found]
```

---

## Automation Scripts

### Quick Health Check
```bash
# Check bridge status
curl http://BRIDGE_IP:8787/health

# Check HA logs for errors
tail -f /config/home-assistant.log | grep tuya_ble_mesh
```

### Enable Debug Logging
```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.tuya_ble_mesh: debug
```

### Test Automation
```yaml
# automations.yaml
- alias: Test Tuya Light
  trigger:
    - platform: state
      entity_id: input_boolean.test_switch
      to: 'on'
  action:
    - service: light.turn_on
      target:
        entity_id: light.tuya_ble_mesh_light
      data:
        brightness: 255
```
