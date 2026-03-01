#!/usr/bin/env bash
# Tuya BLE Mesh — Full validation pipeline.
# ALL checks must pass before committing. No exceptions.
#
# Usage: bash scripts/run-checks.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

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
    ruff check lib/ scripts/ tests/ custom_components/

# Step 2: Format
run_step "ruff format" \
    ruff format --check lib/ scripts/ tests/ custom_components/

# Step 3: Type checking
run_step "mypy --strict" \
    mypy --strict lib/

# Step 4: Security static analysis
run_step "bandit" \
    bandit -r lib/ -c pyproject.toml -q

# Step 5: Dependency vulnerability scan
run_step "safety check" \
    safety check --output bare

# Step 6: Secret detection
run_step "detect-secrets" \
    bash -c 'detect-secrets scan --exclude-files "(\.git/.*|strings\.json|translations/.*\.json)" . 2>&1 | python3 -c "
import sys, json
data = json.load(sys.stdin)
results = data.get(\"results\", {})
found = {k: v for k, v in results.items() if v}
if found:
    for path, secrets in found.items():
        for s in secrets:
            print(f\"  {path}: {s.get(\"type\", \"unknown\")} (line {s.get(\"line_number\", \"?\")})\")
    sys.exit(1)
"'

# Step 7: Unit tests
run_step "pytest" \
    pytest tests/unit/ -v --tb=short

# Summary
printf "\n╔══════════════════════════════════════════╗\n"
printf "║  Results: %d passed, %d failed              ║\n" "$PASS" "$FAIL"
printf "╚══════════════════════════════════════════╝\n"

if [ "$FAIL" -gt 0 ]; then
    printf "\nFix all failures before committing.\n"
    exit 1
fi

printf "\nAll checks passed.\n"
