#!/bin/bash
# Trigger GitHub Actions release workflow after tagging
# Usage: ./scripts/create-github-release.sh v0.11.1
set -euo pipefail

TAG="${1:?Usage: $0 <tag>}"
REPO="11z4t/tuya-ble-mesh"

# Read GitHub PAT from 1Password (or env)
if [ -n "${GITHUB_TOKEN:-}" ]; then
    GH_TOKEN="$GITHUB_TOKEN"
else
    # Try local credential file
    CRED_FILE="$HOME/.config/github-pat"
    if [ -f "$CRED_FILE" ]; then
        GH_TOKEN=$(cat "$CRED_FILE")
    else
        echo "ERROR: No GITHUB_TOKEN env var or $CRED_FILE found"
        exit 1
    fi
fi

# Check if release already exists
STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: token $GH_TOKEN" \
    "https://api.github.com/repos/${REPO}/releases/tags/${TAG}")

if [ "$STATUS" = "200" ]; then
    echo "Release ${TAG} already exists on GitHub"
    exit 0
fi

# Generate changelog
PREV=$(git tag --sort=-version:refname | grep '^v' | grep -v "^${TAG}$" | head -1)
PREV="${PREV:-$(git rev-list --max-parents=0 HEAD)}"
VERSION=$(python3 -c "import json; print(json.load(open('custom_components/tuya_ble_mesh/manifest.json'))['version'])")

CHANGELOG=$(git log "${PREV}..${TAG}" --pretty=format:"- %s" --no-merges | grep -v "^- Merge" || echo "- Release ${TAG}")

BODY="## Tuya BLE Mesh ${VERSION}

### Changes

${CHANGELOG}

### Installation

Install via [HACS](https://hacs.xyz) → Custom repositories → \`https://github.com/${REPO}\`

Full documentation: [Wiki](https://github.com/${REPO}/wiki)"

# Create release
PAYLOAD=$(python3 -c "
import json
print(json.dumps({
    'tag_name': '${TAG}',
    'name': '${TAG}',
    'body': '''${BODY}''',
    'draft': False,
    'prerelease': False
}))
")

RESULT=$(curl -s -X POST "https://api.github.com/repos/${REPO}/releases" \
    -H "Authorization: token $GH_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -d "$PAYLOAD")

URL=$(echo "$RESULT" | python3 -c "import sys,json;print(json.load(sys.stdin).get('html_url','ERROR'))" 2>/dev/null)
echo "GitHub release created: $URL"
