#!/usr/bin/env bash
# Sync lib/tuya_ble_mesh → custom_components/tuya_ble_mesh/lib/tuya_ble_mesh
# Run this after editing lib/ files to keep the bundled copy up to date.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

SRC="$PROJECT_DIR/lib/tuya_ble_mesh"
DST="$PROJECT_DIR/custom_components/tuya_ble_mesh/lib/tuya_ble_mesh"

if [ ! -d "$SRC" ]; then
    echo "ERROR: Source not found: $SRC"
    exit 1
fi

rm -rf "$DST"
cp -r "$SRC" "$DST"
rm -rf "$DST/__pycache__"

echo "Synced lib/tuya_ble_mesh → custom_components/tuya_ble_mesh/lib/tuya_ble_mesh"
