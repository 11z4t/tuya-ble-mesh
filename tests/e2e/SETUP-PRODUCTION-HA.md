# Running E2E Tests Against Production Home Assistant

This guide explains how to run Playwright E2E tests against the production Home Assistant instance at `192.168.5.22`.

## Prerequisites

1. **Network Access**: Ensure you can reach `192.168.5.22:8123` from your test machine
2. **Node.js 18+** installed
3. **Playwright** dependencies installed

```bash
cd /tmp/tuya-ble-mesh-sync
npm install
npx playwright install chromium  # Or all browsers: firefox, webkit
```

## Configuration

The `playwright.config.ts` reads `HA_BASE_URL` from environment. Set it to the production HA instance:

```bash
export HA_BASE_URL="http://192.168.5.22:8123"
```

## Running Tests

### Quick Start (Chromium only)

```bash
cd /tmp/tuya-ble-mesh-sync
export HA_BASE_URL="http://192.168.5.22:8123"
npx playwright test --project=chromium
```

### Run Specific Test Suite

```bash
# Config flow tests only
npx playwright test config-flow --project=chromium

# Accessibility tests
npx playwright test accessibility --project=chromium

# Entity interaction tests
npx playwright test entity-interaction --project=chromium
```

### Interactive Mode (UI)

```bash
npx playwright test --ui
```

This opens Playwright's test runner UI where you can:
- See test execution live
- Pause/resume tests
- Inspect DOM elements
- View network requests

### Debug Mode

```bash
npx playwright test --debug
```

Opens Playwright Inspector for step-by-step debugging.

## Authentication

**Note:** Most tests assume you're already authenticated in Home Assistant. If tests fail with authentication errors:

1. Open browser manually to `http://192.168.5.22:8123`
2. Log in
3. Copy cookies from browser to Playwright storage state (see below)

### Setting Up Persistent Auth

Create `tests/e2e/.auth/user.json` with HA auth cookies:

```json
{
  "cookies": [
    {
      "name": "hassio_ingress",
      "value": "YOUR_SESSION_TOKEN",
      "domain": "192.168.5.22",
      "path": "/",
      "httpOnly": true
    }
  ]
}
```

Then update `playwright.config.ts` to use this storage state:

```typescript
use: {
  storageState: 'tests/e2e/.auth/user.json',
  // ... other settings
}
```

## Multi-Browser Testing

Run tests across all configured browsers (Chromium, Firefox, WebKit):

```bash
npx playwright test
```

**Warning:** This takes longer. Use `--project=chromium` for fast iteration.

## CI/CD Integration

For automated testing, create a `.env` file:

```bash
# .env
HA_BASE_URL=http://192.168.5.22:8123
HA_USER=test_user
HA_PASSWORD=test_password
```

Then reference in CI workflow (`.gitea/workflows/e2e-tests.yml`):

```yaml
- name: Run E2E Tests
  env:
    HA_BASE_URL: http://192.168.5.22:8123
  run: npx playwright test --project=chromium
```

## Test Reports

After running tests, view the HTML report:

```bash
npx playwright show-report
```

Reports include:
- Test results per browser
- Screenshots on failure
- Videos of failed tests
- Trace files for debugging (click to open in Trace Viewer)

## Troubleshooting

### "Target page, context or browser has been closed"

- HA instance may be restarting
- Network timeout - increase timeout in `playwright.config.ts`

### "Selector not found"

- HA UI may have changed - update selectors in test
- Use Playwright Inspector to find correct selector: `npx playwright test --debug`

### "Authentication required"

- Set up persistent auth (see above)
- Or manually log in before running tests

### "Connection refused"

- Verify HA is running: `curl http://192.168.5.22:8123`
- Check firewall rules
- Ensure HA is listening on all interfaces (not just 127.0.0.1)

## Performance Tips

1. **Run single browser**: `--project=chromium` is 3x faster than all browsers
2. **Run specific tests**: `npx playwright test entity-interaction` instead of all
3. **Skip slow tests**: Use `.skip()` for tests that are slow or flaky during development
4. **Headed mode**: Add `--headed` to see browser (useful for debugging, slower)

## Security Note

**Production HA Instance**: These tests run against the production Home Assistant at 192.168.5.22. Be aware:

- Tests may trigger automations
- Tests may change device states
- Tests may create/delete entities

Consider using a **dedicated test HA instance** (e.g., LXC container) for safer E2E testing.

## Next Steps

Once E2E tests pass locally:

1. Add to CI/CD pipeline (`.gitea/workflows/e2e-tests.yml`)
2. Set up scheduled nightly runs
3. Configure test isolation (snapshot/restore HA state between runs)
4. Add visual regression tests for UI consistency
