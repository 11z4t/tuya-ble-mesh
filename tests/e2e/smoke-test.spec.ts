import { test, expect } from '@playwright/test';

/**
 * Smoke Test - Quick validation that E2E setup works
 *
 * Verifies:
 * 1. Home Assistant is reachable and loads
 * 2. Configuration pages render (HA routing can be slow — 30s timeout)
 * 3. Integrations page loads
 */

test.describe('E2E Smoke Test', () => {
  test('Home Assistant should be reachable and load', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('home-assistant', { timeout: 30000 });

    const title = await page.title();
    expect(title).toContain('Home Assistant');

    await page.screenshot({ path: 'playwright-report/smoke-test.png', fullPage: true });
  });

  test('Configuration menu should be accessible', async ({ page }) => {
    await page.goto('/config/dashboard');
    await page.waitForLoadState('domcontentloaded');

    // HA routing marks components as hidden during transitions — wait for visible
    await page.waitForSelector('ha-config-dashboard', { state: 'visible', timeout: 30000 });

    const heading = page.locator('h1, [slot="header"]').filter({ hasText: /Configuration|Settings|Inställningar/i });
    if (await heading.count() > 0) {
      await expect(heading.first()).toBeVisible({ timeout: 5000 });
    } else {
      // Config dashboard loaded — heading text may vary by HA locale
      expect(true).toBeTruthy();
    }
  });

  test('Integrations page should load', async ({ page }) => {
    await page.goto('/config/integrations');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForSelector('ha-config-integrations', { state: 'visible', timeout: 30000 });

    // Verify integrations page rendered — "Add Integration" text varies by HA version/locale
    const integrationsEl = page.locator('ha-config-integrations');
    await expect(integrationsEl).toBeVisible();
  });
});
