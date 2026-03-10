#!/bin/bash
# Installationsskript för tuya-ble-mesh på Home Assistant
set -e

GITEA_URL="http://192.168.5.40:3000/4recon/tuya-ble-mesh/archive/main.zip"
TMP_ZIP="/tmp/tuya-ble-mesh.zip"
TMP_DIR="/tmp/tuya-ble-mesh-install"
HA_CONFIG="/config"

echo "Hämtar senaste från Gitea..."
wget -q -O "$TMP_ZIP" "$GITEA_URL"

echo "Extraherar..."
rm -rf "$TMP_DIR"
unzip -q "$TMP_ZIP" -d "$TMP_DIR"

echo "Kopierar till custom_components..."
mkdir -p "$HA_CONFIG/custom_components"
rsync -a --delete "$TMP_DIR/tuya-ble-mesh/custom_components/tuya_ble_mesh/" \
    "$HA_CONFIG/custom_components/tuya_ble_mesh/"

echo "Städar..."
rm -rf "$TMP_DIR" "$TMP_ZIP"

echo "✅ Installation klar!"
echo "Starta om HA med: curl -X POST -H \"Authorization: Bearer \$SUPERVISOR_TOKEN\" http://supervisor/core/api/services/homeassistant/restart"
