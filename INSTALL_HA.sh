#!/bin/bash
# Install script for tuya-ble-mesh on Home Assistant
# Run this script on the HA host (hassio user) or from HA terminal

set -e

echo "Installing tuya-ble-mesh from GitHub main branch..."

cd /config/custom_components

# Backup existing installation
if [ -d "tuya_ble_mesh" ]; then
    echo "Backing up existing installation..."
    mv tuya_ble_mesh tuya_ble_mesh.backup.$(date +%Y%m%d-%H%M%S)
fi

# Download and extract
echo "Downloading from GitHub..."
wget -q -O tuya_ble_mesh.zip https://github.com/11z4t/tuya-ble-mesh/archive/refs/heads/main.zip

echo "Extracting..."
unzip -q tuya_ble_mesh.zip

echo "Moving to correct location..."
mv tuya-ble-mesh-main/custom_components/tuya_ble_mesh .

# Cleanup
echo "Cleaning up..."
rm -rf tuya-ble-mesh-main tuya_ble_mesh.zip

echo "Installation complete!"
echo "Version installed: $(grep version tuya_ble_mesh/manifest.json | cut -d'"' -f4)"
echo ""
echo "NEXT STEPS:"
echo "1. Restart Home Assistant"
echo "2. Wait for a BLE device to be discovered (may take a few minutes)"
echo "3. Check Settings -> Integrations for the discovery notification"
echo "4. Verify the discovery card shows 'Mesh Plug AB:CD:EF:12' or similar"
echo "   (NOT 'Tuya BLE Mesh' twice)"
