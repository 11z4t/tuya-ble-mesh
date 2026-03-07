import { test, expect } from '@playwright/test';

/**
 * Visual Regression Tests
 *
 * Captures and compares screenshots of key UI components to detect
 * unintended visual changes. Uses Playwright's built-in screenshot
 * comparison with configurable tolerance.
 */

test.describe('Visual Regression Tests', () => {
  test.beforeEach(async ({ page }) => {
    // Navigate to Home Assistant
    await page.goto('/');
    await page.waitForSelector('home-assistant', { timeout: 10000 });
  });

  test('config flow UI should remain consistent', async ({ page }) => {
    // Navigate to integrations
    await page.goto('/config/integrations');
    await page.waitForSelector('ha-config-integrations', { timeout: 10000 });

    // Search for Tuya BLE Mesh integration
    const searchBox = page.locator('input[placeholder*="Search"]').first();
    
    if (await searchBox.count() > 0) {
      await searchBox.fill('tuya ble mesh');
      await page.waitForTimeout(500);
    }

    // Take screenshot of integrations page
    await expect(page).toHaveScreenshot('integrations-page.png', {
      maxDiffPixels: 100, // Allow minor rendering differences
      threshold: 0.2,
    });
  });

  test('entity list should render consistently', async ({ page }) => {
    await page.goto('/config/entities');
    await page.waitForSelector('ha-config-entities', { timeout: 10000 });

    // Filter for Tuya entities
    const searchBox = page.locator('input[placeholder*="Search"]').first();
    
    if (await searchBox.count() > 0) {
      await searchBox.fill('tuya_ble_mesh');
      await page.waitForTimeout(1000);
    }

    // Screenshot entity list
    const entityTable = page.locator('ha-data-table').first();
    
    if (await entityTable.count() > 0) {
      await expect(entityTable).toHaveScreenshot('entity-list.png', {
        maxDiffPixels: 150,
        threshold: 0.2,
      });
    }
  });

  test('device card visual consistency', async ({ page }) => {
    await page.goto('/config/devices/dashboard');
    await page.waitForSelector('ha-config-devices-dashboard', { timeout: 10000 });

    // Search for Tuya device
    const searchBox = page.locator('input[placeholder*="Search"]').first();
    
    if (await searchBox.count() > 0) {
      await searchBox.fill('tuya mesh');
      await page.waitForTimeout(1000);

      // Screenshot device card
      const deviceCard = page.locator('.device-card, ha-card').first();
      
      if (await deviceCard.count() > 0) {
        await expect(deviceCard).toHaveScreenshot('device-card.png', {
          maxDiffPixels: 100,
          threshold: 0.2,
        });
      }
    }
  });

  test('light entity more-info dialog', async ({ page }) => {
    await page.goto('/lovelace/0');

    // Find Tuya light entity
    const lightCard = page.locator('ha-card').filter({
      hasText: /tuya.*mesh|mesh.*light/i
    }).first();

    if (await lightCard.count() > 0) {
      // Open more-info dialog
      await lightCard.click();

      const dialog = page.locator('ha-more-info-dialog');
      
      if (await dialog.count() > 0) {
        await expect(dialog).toBeVisible({ timeout: 3000 });
        
        // Screenshot the dialog
        await expect(dialog).toHaveScreenshot('light-more-info-dialog.png', {
          maxDiffPixels: 200,
          threshold: 0.2,
        });

        // Close dialog
        const closeButton = dialog.locator('button[aria-label*="close"]').first();
        if (await closeButton.count() > 0) {
          await closeButton.click();
        }
      }
    }
  });

  test('sensor entity state display', async ({ page }) => {
    await page.goto('/lovelace/0');

    // Look for sensor card (e.g., RSSI)
    const sensorCard = page.locator('ha-card').filter({
      hasText: /rssi|signal|tuya.*sensor/i
    }).first();

    if (await sensorCard.count() > 0) {
      await expect(sensorCard).toHaveScreenshot('sensor-card.png', {
        maxDiffPixels: 100,
        threshold: 0.2,
      });
    }
  });

  test('device diagnostics page layout', async ({ page }) => {
    await page.goto('/config/devices/dashboard');
    
    const searchBox = page.locator('input[placeholder*="Search"]').first();
    
    if (await searchBox.count() > 0) {
      await searchBox.fill('tuya mesh');
      await page.waitForTimeout(1000);

      const deviceCard = page.locator('a').filter({
        hasText: /tuya.*mesh/i
      }).first();

      if (await deviceCard.count() > 0) {
        await deviceCard.click();
        await page.waitForURL(/config\/devices\/device/);

        // Screenshot device detail page
        const devicePage = page.locator('ha-config-device-page, ha-device-page');
        
        if (await devicePage.count() > 0) {
          await expect(devicePage).toHaveScreenshot('device-detail-page.png', {
            maxDiffPixels: 200,
            threshold: 0.2,
          });
        }
      }
    }
  });

  test('full page snapshot - overview', async ({ page }) => {
    await page.goto('/lovelace/0');
    await page.waitForTimeout(1000); // Ensure all cards loaded

    // Full page screenshot
    await expect(page).toHaveScreenshot('overview-full-page.png', {
      fullPage: true,
      maxDiffPixels: 500,
      threshold: 0.3,
    });
  });
});
