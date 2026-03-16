#!/bin/bash
# PLAT-692: Icon regression guard
# Prevents old SVG icon from being re-introduced

set -e

echo "🔍 Checking for icon regression (PLAT-692)..."

# Check for forbidden SVG icons
SVG_COUNT=$(find custom_components/tuya_ble_mesh -name "icon.svg" -o -name "logo.svg" | wc -l)

if [ "$SVG_COUNT" -gt 0 ]; then
    echo "❌ ERROR: SVG icons detected in custom_components/tuya_ble_mesh/"
    echo "SVG icons override PNG and cause icon regression."
    echo "Forbidden files found:"
    find custom_components/tuya_ble_mesh -name "icon.svg" -o -name "logo.svg"
    echo ""
    echo "Only PNG icons are allowed: icon.png, icon@2x.png, logo.png, logo@2x.png"
    echo "See custom_components/tuya_ble_mesh/.icon-guard for details."
    exit 1
fi

# Check for old icons/ subdirectory
if [ -d "custom_components/tuya_ble_mesh/icons" ]; then
    echo "❌ ERROR: Old icons/ subdirectory detected"
    echo "This directory was removed in commit cd942fee and must not be re-added."
    exit 1
fi

# Verify required PNG icons exist
REQUIRED_ICONS=(
    "custom_components/tuya_ble_mesh/icon.png"
    "custom_components/tuya_ble_mesh/icon@2x.png"
    "custom_components/tuya_ble_mesh/logo.png"
    "custom_components/tuya_ble_mesh/logo@2x.png"
    "custom_components/tuya_ble_mesh/brand/icon.png"
    "custom_components/tuya_ble_mesh/brand/icon@2x.png"
    "custom_components/tuya_ble_mesh/brand/logo.png"
    "custom_components/tuya_ble_mesh/brand/logo@2x.png"
)

MISSING=0
for icon in "${REQUIRED_ICONS[@]}"; do
    if [ ! -f "$icon" ]; then
        echo "❌ ERROR: Missing required icon: $icon"
        MISSING=1
    fi
done

if [ "$MISSING" -eq 1 ]; then
    echo ""
    echo "See custom_components/tuya_ble_mesh/.icon-guard for required icon files."
    exit 1
fi

# Verify icon checksums (optional but recommended)
EXPECTED_MD5="de1a2e116b411dfa1d4da882774d7469"
ACTUAL_MD5=$(md5sum custom_components/tuya_ble_mesh/icon.png | awk '{print $1}')

if [ "$ACTUAL_MD5" != "$EXPECTED_MD5" ]; then
    echo "⚠️  WARNING: icon.png checksum mismatch"
    echo "Expected: $EXPECTED_MD5"
    echo "Actual:   $ACTUAL_MD5"
    echo "The icon may have been modified. Verify it's the correct DALL-E design."
fi

echo "✅ Icon regression check passed"
exit 0
