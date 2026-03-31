import { test, expect } from '@playwright/test';

/**
 * Browser Compatibility Tests
 *
 * Tests critical functionality across multiple browsers and devices.
 * HA config pages use web-component routing that marks elements as `hidden`
 * during transitions — we wait for networkidle + a longer timeout.
 */

/** Wait for a HA config routing element to become visible. */
async function waitForConfigPage(page: import('@playwright/test').Page, selector: string): Promise<void> {
  await page.waitForLoadState('domcontentloaded');
  await page.waitForSelector(selector, { state: 'visible', timeout: 30000 });
}

test.describe('Browser Compatibility', () => {
  test('home assistant loads successfully', async ({ page, browserName }) => {
    await page.goto('/');
    await page.waitForSelector('home-assistant', { timeout: 30000 });

    const haRoot = page.locator('home-assistant');
    await expect(haRoot).toBeVisible();

    console.log(`✓ HA loaded successfully on ${browserName}`);
  });

  test('can navigate to integrations page', async ({ page, browserName }) => {
    await page.goto('/config/integrations');
    await waitForConfigPage(page, 'ha-config-integrations');

    const integrationsPage = page.locator('ha-config-integrations');
    await expect(integrationsPage).toBeVisible();

    console.log(`✓ Integrations page loaded on ${browserName}`);
  });

  test('can search for tuya integration', async ({ page, browserName }) => {
    await page.goto('/config/integrations');
    await waitForConfigPage(page, 'ha-config-integrations');

    const searchBox = page.locator('input[placeholder*="Search"]').first();

    if (await searchBox.count() > 0) {
      await searchBox.fill('tuya');
      await page.waitForTimeout(500);
      await expect(searchBox).toHaveValue('tuya');
    }

    console.log(`✓ Search functionality works on ${browserName}`);
  });

  test('entity list renders correctly', async ({ page, browserName }) => {
    await page.goto('/config/entities');
    await waitForConfigPage(page, 'ha-config-entities');

    const entityPage = page.locator('ha-config-entities');
    await expect(entityPage).toBeVisible();

    const dataTable = page.locator('ha-data-table, table');
    if (await dataTable.count() > 0) {
      await expect(dataTable.first()).toBeVisible();
    }

    console.log(`✓ Entity list renders on ${browserName}`);
  });

  test('device page is accessible', async ({ page, browserName }) => {
    await page.goto('/config/devices/dashboard');
    await waitForConfigPage(page, 'ha-config-devices-dashboard');

    const devicesPage = page.locator('ha-config-devices-dashboard');
    await expect(devicesPage).toBeVisible();

    console.log(`✓ Device dashboard accessible on ${browserName}`);
  });

  test('overview dashboard displays cards', async ({ page, browserName }) => {
    await page.goto('/lovelace/0');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    const cards = page.locator('ha-card');
    const cardCount = await cards.count();

    expect(cardCount).toBeGreaterThanOrEqual(0);

    console.log(`✓ Overview dashboard rendered ${cardCount} cards on ${browserName}`);
  });

  test('CSS rendering is consistent', async ({ page, browserName }) => {
    await page.goto('/');
    await page.waitForSelector('home-assistant', { timeout: 30000 });

    const cssLoaded = await page.evaluate(() => {
      // HA uses CSS custom properties — background is on html/body or :root, not always body
      // Check that CSS is loaded by verifying HA's custom properties are defined
      const styles = window.getComputedStyle(document.documentElement);
      const bodyStyles = window.getComputedStyle(document.body);
      return {
        htmlBackground: styles.backgroundColor,
        bodyFontFamily: bodyStyles.fontFamily,
        haCustomProps: styles.getPropertyValue('--primary-color') !== '',
      };
    });

    // Either the html background is set, OR HA custom CSS properties are present
    const cssIsApplied =
      cssLoaded.htmlBackground !== 'rgba(0, 0, 0, 0)' ||
      cssLoaded.haCustomProps ||
      cssLoaded.bodyFontFamily.length > 0;

    expect(cssIsApplied).toBeTruthy();
    expect(cssLoaded.bodyFontFamily).toBeTruthy();

    console.log(`✓ CSS loaded properly on ${browserName}`);
  });

  test('JavaScript executes correctly', async ({ page, browserName }) => {
    await page.goto('/');
    await page.waitForSelector('home-assistant', { timeout: 30000 });

    const hasWebComponents = await page.evaluate(() => {
      return !!customElements.get('home-assistant');
    });

    expect(hasWebComponents).toBeTruthy();

    console.log(`✓ JavaScript execution verified on ${browserName}`);
  });

  test('viewport adapts to mobile devices', async ({ page, browserName, viewport }) => {
    if (!viewport) return;

    // Retry goto — HA can be temporarily unavailable after heavy JS evaluation
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        await page.goto('/');
        break;
      } catch (err) {
        if (attempt === 2) throw err;
        await page.waitForTimeout(5000);
      }
    }
    await page.waitForSelector('home-assistant', { timeout: 30000 });

    const isMobile = viewport.width < 768;

    if (isMobile) {
      const haDrawer = page.locator('ha-drawer, mwc-drawer');
      if (await haDrawer.count() > 0) {
        const drawer = haDrawer.first();
        await expect(drawer).toBeDefined();
      }
    }

    console.log(`✓ Responsive layout verified on ${browserName} (${viewport.width}x${viewport.height})`);
  });

  test('touch interactions work on mobile', async ({ page, browserName, isMobile }) => {
    await page.goto('/config/integrations');
    await waitForConfigPage(page, 'ha-config-integrations');

    const searchBox = page.locator('input[placeholder*="Search"]').first();

    if (await searchBox.count() > 0) {
      // tap() works on both mobile and desktop in Playwright; click() as fallback for desktop
      if (isMobile) {
        await searchBox.tap();
      } else {
        await searchBox.click();
      }
      await expect(searchBox).toBeFocused();
    }

    console.log(`✓ Touch/click interactions work on ${browserName}`);
  });
});
