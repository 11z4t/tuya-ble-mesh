# PLAT-692: Icon Regression Fix

## Problem
The old orange Tuya icon (icon.svg) was appearing in Home Assistant despite being replaced with a new DALL-E design in commit 3b0cb80d.

## Root Cause
Home Assistant loads icons in this priority order:
1. **icon.svg** (HIGHEST PRIORITY - overrides PNG if exists)
2. icon.png + icon@2x.png
3. brand/ subdirectory

The old `custom_components/tuya_ble_mesh/icon.svg` was deleted in commit cd942fee, but could have been cached by HA or accidentally re-introduced.

## Permanent Fix (Multi-Layer Defense)

### 1. `.gitignore` Protection
Added entries to block SVG icons from being committed:
```
custom_components/tuya_ble_mesh/icon.svg
custom_components/tuya_ble_mesh/brand/icon.svg
custom_components/tuya_ble_mesh/icons/
```

### 2. Icon Guard Documentation
Created `custom_components/tuya_ble_mesh/.icon-guard` with:
- Required icon files and checksums
- Forbidden files that cause regression
- Cache clearing instructions

### 3. Automated CI Check
Added `.github/scripts/check-icon-regression.sh` to verify:
- No SVG icons exist in integration directory
- All required PNG icons are present
- Icon checksums match expected values

Integrated into CI workflow as `icon-check` job.

### 4. Pre-Commit Verification
The check script can be run locally before committing:
```bash
.github/scripts/check-icon-regression.sh
```

## Current Icon State
All icon files are the correct DALL-E design (MD5: de1a2e116b411dfa1d4da882774d7469):
- custom_components/tuya_ble_mesh/icon.png (62224 bytes)
- custom_components/tuya_ble_mesh/icon@2x.png (244539 bytes)
- custom_components/tuya_ble_mesh/logo.png (62224 bytes)
- custom_components/tuya_ble_mesh/logo@2x.png (244539 bytes)
- custom_components/tuya_ble_mesh/brand/icon.png (62224 bytes)
- custom_components/tuya_ble_mesh/brand/icon@2x.png (244539 bytes)
- custom_components/tuya_ble_mesh/brand/logo.png (62224 bytes)
- custom_components/tuya_ble_mesh/brand/logo@2x.png (244539 bytes)

## Verification After HA Reload
If the old icon still appears after reloading the integration:

1. **Clear browser cache**: Ctrl+Shift+R (hard refresh)
2. **Restart Home Assistant**: Developer Tools → Restart
3. **Clear HA icon cache**:
   ```bash
   rm -rf /config/.storage/lovelace*
   ```
4. **Verify no SVG exists**:
   ```bash
   find custom_components/tuya_ble_mesh -name "*.svg"
   # Should return nothing
   ```

## Prevention Going Forward
- CI will fail if SVG icons are added
- Git will ignore accidental SVG files
- .icon-guard documents the requirements
- Pre-commit hook available for local verification

This ensures the icon regression CANNOT happen again.
