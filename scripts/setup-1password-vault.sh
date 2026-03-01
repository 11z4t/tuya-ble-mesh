#!/usr/bin/env bash
# ============================================================================
# 1Password Vault Setup for Malmbergs BT Project
# ============================================================================
# Run this ONCE as the human operator to create the vault structure.
# After this, Claude Code uses Service Account for headless access.
#
# ⛔ Claude Code: NEVER run this script. It contains interactive prompts.
# ============================================================================

set -euo pipefail

echo "=== Malmbergs BT — 1Password Vault Setup ==="
echo ""
echo "This script creates the vault and placeholder items."
echo "You will need to fill in actual secret values manually."
echo ""

# Check op is authenticated
if ! op account list &>/dev/null; then
    echo "Please sign in first: eval \$(op signin)"
    exit 1
fi

# Create vault
echo "Creating vault 'malmbergs-bt'..."
op vault create malmbergs-bt 2>/dev/null || echo "Vault already exists"

# Create placeholder items
echo "Creating secret placeholders..."

op item create --vault malmbergs-bt \
    --category=api-credential \
    --title="anthropic-api" \
    "credential=REPLACE_WITH_ACTUAL_KEY" 2>/dev/null || echo "  anthropic-api already exists"

op item create --vault malmbergs-bt \
    --category=login \
    --title="nas-samba" \
    "username=REPLACE" \
    "password=REPLACE" 2>/dev/null || echo "  nas-samba already exists"

op item create --vault malmbergs-bt \
    --category=api-credential \
    --title="gitea" \
    "token=REPLACE" 2>/dev/null || echo "  gitea already exists"

echo ""
echo "=== Done ==="
echo ""
echo "Next steps:"
echo "1. Edit each item in 1Password and set real values"
echo "2. Create a Service Account in 1Password web UI"
echo "3. Grant Service Account access to 'malmbergs-bt' vault"
echo "4. In tmux, before starting Claude Code:"
echo "   export OP_SERVICE_ACCOUNT_TOKEN='ops_your_token_here'"
echo ""
echo "Verify with: op read 'op://malmbergs-bt/anthropic-api/credential' > /dev/null && echo OK"
