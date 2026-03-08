# End-to-End Tests

Comprehensive E2E test suite for Tuya BLE Mesh Home Assistant integration using Playwright.

## Test Coverage

| Test Suite | File | Description |
|------------|------|-------------|
| **Config Flow** | `config-flow.spec.ts` | Integration setup wizard, discovery, validation |
| **Entity Interaction** | `entity-interaction.spec.ts` | Light control, switches, sensors, state updates |
| **Accessibility** | `accessibility.spec.ts` | WCAG 2.1 AA compliance, keyboard nav, screen readers |
| **Visual Regression** | `visual-regression.spec.ts` | UI consistency across versions |
| **Multi-Browser** | `browser-compatibility.spec.ts` | Chromium, Firefox, WebKit, mobile |

## Prerequisites

1. **Home Assistant instance** running with tuya-ble-mesh integration installed
2. **Node.js** 18+ and npm
3. **Playwright** browsers installed

```bash
npm install
npx playwright install
```

## Environment Setup

Set `HA_BASE_URL` to your test Home Assistant instance:

```bash
export HA_BASE_URL="http://localhost:8123"
```

## Running Tests

### All tests (single browser)
```bash
npx playwright test --project=chromium
```

### Specific test suite
```bash
npx playwright test config-flow
npx playwright test accessibility
npx playwright test visual-regression
```

### All browsers (multi-browser testing)
```bash
npx playwright test
```

### With UI mode (interactive)
```bash
npx playwright test --ui
```

### Debug mode
```bash
npx playwright test --debug
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
- Trace files for debugging

## CI/CD Integration

Tests run automatically on:
- Pull requests (Chromium only)
- Main branch pushes (all browsers)
- Scheduled nightly runs

See `.gitea/workflows/e2e-tests.yml` for CI configuration.

## Writing New Tests

1. Create a new `.spec.ts` file in `tests/e2e/`
2. Follow existing patterns for selectors and assertions
3. Use page objects for reusable components
4. Mark flaky tests with `.skip()` or `.fixme()` temporarily

Example:

```typescript
import { test, expect } from '@playwright/test';

test.describe('New Feature', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/config/integrations');
  });

  test('should do something', async ({ page }) => {
    // Your test logic
    await expect(page.locator('selector')).toBeVisible();
  });
});
```

## Best Practices

1. **Use data-testid**: Prefer `[data-testid="..."]` selectors over text/class
2. **Wait properly**: Use `waitForSelector` or `expect(...).toBeVisible()`
3. **Isolate tests**: Each test should be independent and idempotent
4. **Clean state**: Reset state between tests if needed
5. **Handle flakiness**: Add retries for network-dependent tests

## Troubleshooting

**Tests fail with timeout**
- Increase timeout in `playwright.config.ts`
- Check if HA is running and accessible
- Verify `HA_BASE_URL` is correct

**Selector not found**
- Use Playwright Inspector: `npx playwright test --debug`
- Check HA version compatibility
- Verify integration is installed

**Video/screenshot missing**
- Check `playwright-report/` directory
- Ensure `video: 'retain-on-failure'` is set in config

## Related Documentation

- [Accessibility Tests](README.accessibility.md)
- [Visual Regression](README.visual-regression.md)
- [Multi-Browser](README.multi-browser.md)
- [Playwright Docs](https://playwright.dev)
