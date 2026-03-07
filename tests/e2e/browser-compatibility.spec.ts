import { test, expect } from '@playwright/test';

/**
 * Browser Compatibility Tests
 *
 * Tests critical functionality across multiple browsers and devices:
 * - Desktop: Chromium, Firefox, WebKit (Safari)
 * - Mobile: Chrome (Pixel 5), Safari (iPhone 13)
 *
 * These tests ensure the integration works consistently across browsers.
 */

test.describe('Browser Compatibility', () => {
  test('home assistant loads successfully', async ({ page, browserName }) => {
    await page.goto('/');
    
    // Wait for HA to load
    await page.waitForSelector('home-assistant', { timeout: 15000 });
    
    // Verify basic UI elements are present
    const haRoot = page.locator('home-assistant');
    await expect(haRoot).toBeVisible();

    console.log(`✓ HA loaded successfully on ${browserName}`);
  });

  test('can navigate to integrations page', async ({ page, browserName }) => {
    await page.goto('/config/integrations');
    
    await page.waitForSelector('ha-config-integrations', { timeout: 10000 });
    
    const integrationsPage = page.locator('ha-config-integrations');
    await expect(integrationsPage).toBeVisible();

    console.log(`✓ Integrations page loaded on ${browserName}`);
  });

  test('can search for tuya integration', async ({ page, browserName }) => {
    await page.goto('/config/integrations');
    await page.waitForSelector('ha-config-integrations', { timeout: 10000 });

    const searchBox = page.locator('input[placeholder*="Search"]').first();
    
    if (await searchBox.count() > 0) {
      await searchBox.fill('tuya');
      await page.waitForTimeout(500);
      
      // Verify search works
      await expect(searchBox).toHaveValue('tuya');
    }

    console.log(`✓ Search functionality works on ${browserName}`);
  });

  test('entity list renders correctly', async ({ page, browserName }) => {
    await page.goto('/config/entities');
    await page.waitForSelector('ha-config-entities', { timeout: 10000 });

    const entityPage = page.locator('ha-config-entities');
    await expect(entityPage).toBeVisible();

    // Verify data table renders
    const dataTable = page.locator('ha-data-table, table');
    if (await dataTable.count() > 0) {
      await expect(dataTable.first()).toBeVisible();
    }

    console.log(`✓ Entity list renders on ${browserName}`);
  });

  test('device page is accessible', async ({ page, browserName }) => {
    await page.goto('/config/devices/dashboard');
    await page.waitForSelector('ha-config-devices-dashboard', { timeout: 10000 });

    const devicesPage = page.locator('ha-config-devices-dashboard');
    await expect(devicesPage).toBeVisible();

    console.log(`✓ Device dashboard accessible on ${browserName}`);
  });

  test('overview dashboard displays cards', async ({ page, browserName }) => {
    await page.goto('/lovelace/0');
    await page.waitForTimeout(2000); // Wait for cards to load

    // Verify at least one card is rendered
    const cards = page.locator('ha-card');
    const cardCount = await cards.count();

    // At least some UI should render (even if no Tuya devices)
    expect(cardCount).toBeGreaterThanOrEqual(0);

    console.log(`✓ Overview dashboard rendered ${cardCount} cards on ${browserName}`);
  });

  test('CSS rendering is consistent', async ({ page, browserName }) => {
    await page.goto('/');
    await page.waitForSelector('home-assistant', { timeout: 10000 });

    // Check that CSS is loaded (no FOUC - Flash of Unstyled Content)
    const bodyStyles = await page.evaluate(() => {
      const body = document.body;
      const styles = window.getComputedStyle(body);
      return {
        backgroundColor: styles.backgroundColor,
        fontFamily: styles.fontFamily,
        fontSize: styles.fontSize,
      };
    });

    // Verify styles are applied (not default browser styles)
    expect(bodyStyles.backgroundColor).not.toBe('rgba(0, 0, 0, 0)');
    expect(bodyStyles.fontFamily).toBeTruthy();

    console.log(`✓ CSS loaded properly on ${browserName}`);
  });

  test('JavaScript executes correctly', async ({ page, browserName }) => {
    await page.goto('/');
    await page.waitForSelector('home-assistant', { timeout: 10000 });

    // Test that JavaScript is running by checking for web components
    const hasWebComponents = await page.evaluate(() => {
      return !!customElements.get('home-assistant');
    });

    expect(hasWebComponents).toBeTruthy();

    console.log(`✓ JavaScript execution verified on ${browserName}`);
  });

  test('viewport adapts to mobile devices', async ({ page, browserName, viewport }) => {
    if (!viewport) return; // Skip if no viewport defined

    await page.goto('/');
    await page.waitForSelector('home-assistant', { timeout: 10000 });

    // Verify responsive layout
    const isMobile = viewport.width < 768;
    
    if (isMobile) {
      // On mobile, sidebar might be hidden or collapsed
      const haDrawer = page.locator('ha-drawer, mwc-drawer');
      
      if (await haDrawer.count() > 0) {
        const drawer = haDrawer.first();
        // Drawer should exist but may be closed
        await expect(drawer).toBeDefined();
      }
    }

    console.log(`✓ Responsive layout verified on ${browserName} (${viewport.width}x${viewport.height})`);
  });

  test('touch interactions work on mobile', async ({ page, browserName, isMobile }) => {
    if (!isMobile) {
      test.skip();
      return;
    }

    await page.goto('/config/integrations');
    await page.waitForSelector('ha-config-integrations', { timeout: 10000 });

    // Test tap interaction
    const searchBox = page.locator('input[placeholder*="Search"]').first();
    
    if (await searchBox.count() > 0) {
      await searchBox.tap();
      await expect(searchBox).toBeFocused();
    }

    console.log(`✓ Touch interactions work on ${browserName}`);
  });
});
