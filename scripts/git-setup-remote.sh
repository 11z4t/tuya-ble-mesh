#!/usr/bin/env bash
# Run this after adding the SSH key to Gitea
source "$(dirname "$0")/../.env" 2>/dev/null || source "$(dirname "$0")/../../rpi-setup/.env"

GITEA_HOST=$(echo "${GITEA_URL}" | sed -E 's|https?://||' | sed 's|/.*||' | sed 's|:.*||')
REMOTE_URL="git@${GITEA_HOST}:${GITEA_USER}/${GITEA_REPO_NAME}.git"

echo "Setting remote origin to: ${REMOTE_URL}"
git remote remove origin 2>/dev/null || true
git remote add origin "${REMOTE_URL}"

echo "Testing connection..."
ssh -T "git@${GITEA_HOST}" 2>&1 || true

echo "Done. You can now: git push -u origin main"
