#!/usr/bin/env bash
# Tuya BLE Mesh — Full validation pipeline.
# ALL checks must pass before committing. No exceptions.
#
# Usage: bash scripts/run-checks.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# Tool paths — some in .venv, some in ~/.local/bin or system PATH
VENV="$PROJECT_DIR/.venv/bin"
RUFF="${RUFF:-$(command -v ruff 2>/dev/null || echo "$VENV/ruff")}"
MYPY="$VENV/mypy"
BANDIT="${BANDIT:-$(command -v bandit 2>/dev/null || echo "$VENV/bandit")}"
PIP_AUDIT="${PIP_AUDIT:-$(command -v pip-audit 2>/dev/null || echo "$VENV/pip-audit")}"
DETECT_SECRETS="${DETECT_SECRETS:-$(command -v detect-secrets 2>/dev/null || echo "")}"
PYTEST="$VENV/pytest"
LIB_DIR="custom_components/tuya_ble_mesh/lib"

PASS=0
FAIL=0
STEPS=()

run_step() {
    local name="$1"
    shift
    printf "\n━━━ %s ━━━\n" "$name"
    if "$@"; then
        printf "  ✓ %s passed\n" "$name"
        PASS=$((PASS + 1))
    else
        printf "  ✗ %s FAILED\n" "$name"
        FAIL=$((FAIL + 1))
    fi
    STEPS+=("$name")
}

printf "╔══════════════════════════════════════════╗\n"
printf "║  Tuya BLE Mesh — Validation Pipeline      ║\n"
printf "╚══════════════════════════════════════════╝\n"

# Step 1: Lint
run_step "ruff check" \
    "$RUFF" check tests/ custom_components/

# Step 2: Format
run_step "ruff format" \
    "$RUFF" format --check tests/ custom_components/

# Step 3: Type checking (lib is inside custom_components since PLAT-784)
run_step "mypy --strict" \
    "$MYPY" --strict "$LIB_DIR"

# Step 4: Security static analysis
run_step "bandit" \
    "$BANDIT" -r "$LIB_DIR" -c pyproject.toml -q

# Step 5: Dependency vulnerability scan — audit only production requirements
# (manifest.json requirements, not the full test venv)
_PROD_REQS=$(python3 -c "import json; m=json.load(open('custom_components/tuya_ble_mesh/manifest.json')); print('\n'.join(m.get('requirements',[])))")
_REQ_FILE=$(mktemp)
echo "$_PROD_REQS" > "$_REQ_FILE"
run_step "pip-audit" \
    bash -c "\"$PIP_AUDIT\" -r \"$_REQ_FILE\" \
        --ignore-vuln GHSA-rf74-v2fm-23pw \
        --ignore-vuln CVE-2026-33230 \
        --ignore-vuln CVE-2026-33231 \
        --ignore-vuln CVE-2025-8869 \
        --ignore-vuln CVE-2026-1703"
rm -f "$_REQ_FILE"

# Step 6: Secret detection (skip gracefully if not installed)
if [ -n "$DETECT_SECRETS" ]; then
    run_step "detect-secrets" \
        bash -c '"'"$DETECT_SECRETS"'" scan \
            --exclude-files "(\.git/.*|strings\.json|translations/.*\.json|tests/.*\.py|\.github/.*\.sh)" \
            . 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
results = data.get(\"results\", {})
found = {k: v for k, v in results.items() if v}
if found:
    for path, secrets in found.items():
        for s in secrets:
            print(f\"  {path}: {s.get(\"type\", \"unknown\")} (line {s.get(\"line_number\", \"?\")})\")
    sys.exit(1)
print(\"detect-secrets: no secrets found\")
"'
else
    printf "\n━━━ detect-secrets ━━━\n"
    printf "  ⚠ detect-secrets not installed — skipping\n"
fi

# Step 7: Unit tests
run_step "pytest unit" \
    "$PYTEST" tests/unit/ -q --tb=short

# Step 8: Security tests
run_step "pytest security" \
    "$PYTEST" tests/security/ -q --tb=short

# Summary
printf "\n╔══════════════════════════════════════════╗\n"
printf "║  Results: %d passed, %d failed              ║\n" "$PASS" "$FAIL"
printf "╚══════════════════════════════════════════╝\n"

if [ "$FAIL" -gt 0 ]; then
    printf "\nFix all failures before committing.\n"
    exit 1
fi

printf "\nAll checks passed.\n"
