import { test, expect } from '@playwright/test';

/**
 * Smoke Test - Quick validation that E2E setup works
 *
 * This is a minimal test to verify:
 * 1. Home Assistant is reachable
 * 2. Playwright can navigate and interact
 * 3. Basic HA UI loads correctly
 *
 * Run with: npx playwright test smoke-test --project=chromium
 */

test.describe('E2E Smoke Test', () => {
  test('Home Assistant should be reachable and load', async ({ page }) => {
    // Navigate to HA
    await page.goto('/');

    // Wait for HA app to initialize (max 15 seconds)
    await page.waitForSelector('home-assistant', { timeout: 15000 });

    // Verify page title contains "Home Assistant"
    const title = await page.title();
    expect(title).toContain('Home Assistant');

    // Take screenshot for verification
    await page.screenshot({ path: 'playwright-report/smoke-test.png', fullPage: true });
  });

  test('Configuration menu should be accessible', async ({ page }) => {
    // Navigate to configuration
    await page.goto('/config/dashboard');

    // Wait for config page to load
    await page.waitForSelector('ha-config-dashboard', { timeout: 10000 });

    // Verify "Configuration" or "Settings" heading is visible
    const heading = page.locator('h1, [slot="header"]').filter({ hasText: /Configuration|Settings/i });
    await expect(heading.first()).toBeVisible({ timeout: 5000 });
  });

  test('Integrations page should load', async ({ page }) => {
    // Navigate to integrations
    await page.goto('/config/integrations');

    // Wait for integrations page
    await page.waitForSelector('ha-config-integrations', { timeout: 10000 });

    // Verify "Add Integration" button exists
    const addButton = page.locator('button').filter({ hasText: /Add Integration/i });
    await expect(addButton.first()).toBeVisible({ timeout: 5000 });
  });
});
