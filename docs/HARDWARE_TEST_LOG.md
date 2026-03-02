# Hardware Test Log — Tuya BLE Mesh

Record of hardware validation runs. Fill in after running
`python scripts/hw_validate.py` or `pytest tests/hardware/ -v -s`.

---

## Test Run Template

Copy this template for each validation run.

```
### Run: YYYY-MM-DD HH:MM

**Device:** Malmbergs LED Driver 9952126 (DC:23:4D:21:43:A5)
**Firmware:** 1.6
**Environment:** RPi 4, hci0, Python 3.x

| Step | Result | Notes |
|------|--------|-------|
| BLE Scan | [ ] PASS / [ ] FAIL | |
| GATT Connect | [ ] PASS / [ ] FAIL | |
| Provision (3-step) | [ ] PASS / [ ] FAIL | |
| Power ON | [ ] PASS / [ ] FAIL | Visual: |
| Power OFF | [ ] PASS / [ ] FAIL | Visual: |
| Brightness (dim) | [ ] PASS / [ ] FAIL | Visual: |
| Brightness (full) | [ ] PASS / [ ] FAIL | Visual: |
| Color temp (warm) | [ ] PASS / [ ] FAIL | Visual: |
| Color temp (cool) | [ ] PASS / [ ] FAIL | Visual: |
| Disconnect | [ ] PASS / [ ] FAIL | |
| Reconnect (power cycle) | [ ] PASS / [ ] FAIL | |
| Coordinator start/stop | [ ] PASS / [ ] FAIL | |

**Overall:** [ ] PASS / [ ] FAIL
**Operator:** Börje
**Notes:**
```

---

## Validation Runs

*(Fill in below after each hardware test run)*
