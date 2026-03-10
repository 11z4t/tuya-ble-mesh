# E2E Testing with Playwright

This directory contains end-to-end tests for the Tuya BLE Mesh Home Assistant integration using Playwright.

## Prerequisites

1. **Home Assistant Instance**: A running HA instance with the tuya-ble-mesh integration installed
2. **Node.js**: Version 18 or later
3. **Playwright**: Installed via npm

## Setup

```bash
# Install dependencies
npm install

# Install Playwright browsers
npm run install:playwright
```

## Running Tests

```bash
# Run all E2E tests
npm run test:e2e

# Run with UI visible (headed mode)
npm run test:e2e:headed

# Debug mode (step through tests)
npm run test:e2e:debug

# Interactive UI mode
npm run test:e2e:ui

# View test report
npm run test:e2e:report
```

## Configuration

Set the Home Assistant instance URL via environment variable:

```bash
export HA_BASE_URL=http://localhost:8123
npm run test:e2e
```

Default: `http://localhost:8123`

## Test Structure

- `tests/e2e/config-flow.spec.ts` - Config flow setup wizard tests
- `tests/e2e/entity-interaction.spec.ts` - Entity control and state tests

## Authentication

Tests assume an authenticated HA session. You may need to:
1. Use a long-lived access token
2. Or handle login in test setup

## CI/CD Integration

In CI pipelines:
- Tests run headlessly
- Retry failed tests 2x
- Generate HTML reports in `playwright-report/`
