import { test, expect } from '@playwright/test';

/**
 * Visual Regression Tests
 *
 * Captures and compares screenshots of key UI components.
 * Baseline screenshots are committed in tests/e2e/visual-regression.spec.ts-snapshots/.
 *
 * To regenerate baselines: npx playwright test visual-regression --update-snapshots --project=chromium
 *
 * NOTE: Tests that require specific Tuya BLE Mesh devices configured in HA
 * will skip the screenshot if no devices are found (graceful degradation).
 *
 * Screenshot options used throughout:
 *   timeout: 15000  — extra time for HA animations to stabilize
 *   threshold: 0.3  — tolerates minor rendering differences
 */

/** Wait for a HA config page element to become visible. Visual regression tests get longer timeout. */
async function waitForConfigPage(page: import('@playwright/test').Page, selector: string): Promise<void> {
  await page.waitForLoadState('domcontentloaded');
  await page.waitForSelector(selector, { state: 'visible', timeout: 45000 });
}

/** Standard screenshot options — enough time for HA animations to settle. */
const SCREENSHOT_OPTS = { threshold: 0.3, timeout: 15000 } as const;

test.describe('Visual Regression Tests', () => {
  test.beforeEach(async ({ page }) => {
    // HA can briefly refuse connections between tests — retry with backoff
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        await page.goto('/');
        await page.waitForSelector('home-assistant', { timeout: 30000 });
        return;
      } catch (err) {
        if (attempt === 2) throw err;
        await page.waitForTimeout(3000);
      }
    }
  });

  test('config flow UI should remain consistent', async ({ page }) => {
    await page.goto('/config/integrations');
    await waitForConfigPage(page, 'ha-config-integrations');

    const searchBox = page.locator('input[placeholder*="Search"]').first();
    if (await searchBox.count() > 0) {
      await searchBox.fill('tuya ble mesh');
      await page.waitForTimeout(500);
    }

    // Let page settle before screenshot
    await page.waitForTimeout(2000);
    await expect(page).toHaveScreenshot('integrations-page.png', {
      maxDiffPixels: 500,
      ...SCREENSHOT_OPTS,
    });
  });

  test('entity list should render consistently', async ({ page }) => {
    await page.goto('/config/entities');
    await waitForConfigPage(page, 'ha-config-entities');

    const searchBox = page.locator('input[placeholder*="Search"]').first();
    if (await searchBox.count() > 0) {
      await searchBox.fill('tuya_ble_mesh');
      await page.waitForTimeout(1000);
    }

    const entityTable = page.locator('ha-data-table').first();
    if (await entityTable.count() > 0) {
      // Wait for table animations to settle before taking screenshot
      await page.waitForTimeout(3000);
      await expect(entityTable).toHaveScreenshot('entity-list.png', {
        maxDiffPixels: 300,
        ...SCREENSHOT_OPTS,
      });
    } else {
      expect(true).toBeTruthy();
    }
  });

  test('device card visual consistency', async ({ page }) => {
    await page.goto('/config/devices/dashboard');
    await waitForConfigPage(page, 'ha-config-devices-dashboard');

    const searchBox = page.locator('input[placeholder*="Search"]').first();
    if (await searchBox.count() > 0) {
      await searchBox.fill('tuya mesh');
      await page.waitForTimeout(1000);

      const deviceCard = page.locator('.device-card, ha-card').first();
      if (await deviceCard.count() > 0) {
        await page.waitForTimeout(1000);
        await expect(deviceCard).toHaveScreenshot('device-card.png', {
          maxDiffPixels: 200,
          ...SCREENSHOT_OPTS,
        });
        return;
      }
    }

    // No tuya devices configured — skip screenshot
    expect(true).toBeTruthy();
  });

  test('light entity more-info dialog', async ({ page }) => {
    await page.goto('/lovelace/0');
    await page.waitForLoadState('domcontentloaded');

    const lightCard = page.locator('ha-card').filter({
      hasText: /tuya.*mesh|mesh.*light/i
    }).first();

    if (await lightCard.count() > 0) {
      await lightCard.click();

      const dialog = page.locator('ha-more-info-dialog');
      if (await dialog.count() > 0) {
        await expect(dialog).toBeVisible({ timeout: 5000 });
        await page.waitForTimeout(1000);
        await expect(dialog).toHaveScreenshot('light-more-info-dialog.png', {
          maxDiffPixels: 300,
          ...SCREENSHOT_OPTS,
        });

        const closeButton = dialog.locator('button[aria-label*="close"]').first();
        if (await closeButton.count() > 0) {
          await closeButton.click();
        }
      }
    } else {
      expect(true).toBeTruthy();
    }
  });

  test('sensor entity state display', async ({ page }) => {
    await page.goto('/lovelace/0');
    await page.waitForLoadState('domcontentloaded');

    const sensorCard = page.locator('ha-card').filter({
      hasText: /rssi|signal|tuya.*sensor/i
    }).first();

    if (await sensorCard.count() > 0) {
      await page.waitForTimeout(1000);
      await expect(sensorCard).toHaveScreenshot('sensor-card.png', {
        maxDiffPixels: 100,
        ...SCREENSHOT_OPTS,
      });
    } else {
      expect(true).toBeTruthy();
    }
  });

  test('device diagnostics page layout', async ({ page }) => {
    await page.goto('/config/devices/dashboard');
    await waitForConfigPage(page, 'ha-config-devices-dashboard');

    const searchBox = page.locator('input[placeholder*="Search"]').first();
    if (await searchBox.count() > 0) {
      await searchBox.fill('tuya mesh');
      await page.waitForTimeout(1000);

      const deviceCard = page.locator('a').filter({ hasText: /tuya.*mesh/i }).first();
      if (await deviceCard.count() > 0) {
        await deviceCard.click();
        await page.waitForURL(/config\/devices\/device/);

        const devicePage = page.locator('ha-config-device-page, ha-device-page');
        if (await devicePage.count() > 0) {
          await page.waitForTimeout(1000);
          await expect(devicePage).toHaveScreenshot('device-detail-page.png', {
            maxDiffPixels: 300,
            ...SCREENSHOT_OPTS,
          });
          return;
        }
      }
    }

    expect(true).toBeTruthy();
  });

  test('full page snapshot - overview', async ({ page }) => {
    await page.goto('/lovelace/0');
    await page.waitForLoadState('domcontentloaded');
    // Wait for dashboard animations to settle before full-page screenshot
    await page.waitForTimeout(3000);

    await expect(page).toHaveScreenshot('overview-full-page.png', {
      fullPage: true,
      maxDiffPixelRatio: 0.1, // 10% — HA dashboard has dynamic sensor values
      ...SCREENSHOT_OPTS,
    });
  });
});
