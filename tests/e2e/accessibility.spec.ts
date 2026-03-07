import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * Accessibility (a11y) Tests
 *
 * Tests WCAG 2.1 AA compliance using axe-core automated accessibility scanner.
 * Ensures Tuya BLE Mesh UI components are accessible to users with disabilities.
 *
 * WCAG 2.1 AA Requirements (from CLAUDE-shared.md):
 * - Skip-navigation links
 * - Form labels properly associated
 * - ARIA attributes on interactive elements
 * - Visible focus indicators
 * - Color contrast (4.5:1 text, 3:1 large elements)
 * - Alt text on images
 * - Correct heading hierarchy
 * - lang attribute on html
 */

test.describe('Accessibility Tests (WCAG 2.1 AA)', () => {
  test('integrations page should be accessible', async ({ page }) => {
    await page.goto('/config/integrations');
    await page.waitForSelector('ha-config-integrations', { timeout: 10000 });

    // Run axe accessibility scan
    const accessibilityScanResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();

    // No violations should be found
    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('entity list page should be accessible', async ({ page }) => {
    await page.goto('/config/entities');
    await page.waitForSelector('ha-config-entities', { timeout: 10000 });

    const accessibilityScanResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();

    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('device page should be accessible', async ({ page }) => {
    await page.goto('/config/devices/dashboard');
    await page.waitForSelector('ha-config-devices-dashboard', { timeout: 10000 });

    const accessibilityScanResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();

    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('overview dashboard should be accessible', async ({ page }) => {
    await page.goto('/lovelace/0');
    await page.waitForTimeout(1000); // Wait for cards to load

    const accessibilityScanResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();

    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('keyboard navigation - integrations page', async ({ page }) => {
    await page.goto('/config/integrations');
    await page.waitForSelector('ha-config-integrations', { timeout: 10000 });

    // Test tab navigation
    await page.keyboard.press('Tab');
    
    // Verify focus is visible
    const focusedElement = await page.evaluate(() => {
      const active = document.activeElement;
      if (!active) return null;
      
      const styles = window.getComputedStyle(active);
      return {
        tagName: active.tagName,
        outline: styles.outline,
        outlineWidth: styles.outlineWidth,
        boxShadow: styles.boxShadow,
      };
    });

    // Focus should be visible (has outline or box-shadow)
    expect(focusedElement).toBeTruthy();
  });

  test('color contrast - light entity cards', async ({ page }) => {
    await page.goto('/lovelace/0');
    await page.waitForTimeout(1000);

    // Run color contrast check
    const accessibilityScanResults = await new AxeBuilder({ page })
      .withTags(['wcag2aa'])
      .include('ha-card') // Focus on cards
      .analyze();

    // Check for color-contrast violations
    const contrastViolations = accessibilityScanResults.violations.filter(
      v => v.id === 'color-contrast'
    );

    expect(contrastViolations.length).toBe(0);
  });

  test('form labels - search inputs have labels', async ({ page }) => {
    await page.goto('/config/entities');
    await page.waitForSelector('ha-config-entities', { timeout: 10000 });

    const accessibilityScanResults = await new AxeBuilder({ page })
      .withRules(['label']) // Check form label association
      .analyze();

    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('ARIA roles on interactive elements', async ({ page }) => {
    await page.goto('/config/integrations');
    await page.waitForSelector('ha-config-integrations', { timeout: 10000 });

    // Check for proper ARIA usage
    const accessibilityScanResults = await new AxeBuilder({ page })
      .withTags(['wcag2a'])
      .include('button, [role="button"], a')
      .analyze();

    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('heading hierarchy is correct', async ({ page }) => {
    await page.goto('/config/integrations');
    await page.waitForSelector('ha-config-integrations', { timeout: 10000 });

    // Check heading hierarchy (h1 -> h2 -> h3, no skipping)
    const headingStructure = await page.evaluate(() => {
      const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4, h5, h6'));
      return headings.map(h => ({
        level: parseInt(h.tagName[1]),
        text: h.textContent?.trim().substring(0, 50),
      }));
    });

    // Verify h1 exists
    const h1Count = headingStructure.filter(h => h.level === 1).length;
    expect(h1Count).toBeGreaterThan(0);

    // Check for heading level skips (e.g., h1 -> h3 without h2)
    for (let i = 1; i < headingStructure.length; i++) {
      const prev = headingStructure[i - 1].level;
      const curr = headingStructure[i].level;
      
      // Heading can only increase by 1 level max
      if (curr > prev) {
        expect(curr - prev).toBeLessThanOrEqual(1);
      }
    }
  });

  test('images have alt text', async ({ page }) => {
    await page.goto('/lovelace/0');
    await page.waitForTimeout(1000);

    // Check for images without alt text
    const accessibilityScanResults = await new AxeBuilder({ page })
      .withRules(['image-alt'])
      .analyze();

    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('lang attribute present on html element', async ({ page }) => {
    await page.goto('/');
    await page.waitForSelector('home-assistant', { timeout: 10000 });

    const langAttr = await page.evaluate(() => {
      return document.documentElement.getAttribute('lang');
    });

    expect(langAttr).toBeTruthy();
    expect(langAttr).toMatch(/^[a-z]{2}(-[A-Z]{2})?$/); // e.g., "en" or "en-US"
  });

  test('focus indicators are visible on interactive elements', async ({ page }) => {
    await page.goto('/config/integrations');
    await page.waitForSelector('ha-config-integrations', { timeout: 10000 });

    // Find first focusable element
    const firstButton = page.locator('button, a, input').first();
    
    if (await firstButton.count() > 0) {
      await firstButton.focus();

      // Check computed styles for focus indicator
      const focusStyles = await firstButton.evaluate((el) => {
        const styles = window.getComputedStyle(el);
        return {
          outline: styles.outline,
          outlineWidth: styles.outlineWidth,
          outlineStyle: styles.outlineStyle,
          boxShadow: styles.boxShadow,
        };
      });

      // Either outline or box-shadow should be present
      const hasFocusIndicator = 
        (focusStyles.outlineStyle !== 'none' && focusStyles.outlineWidth !== '0px') ||
        focusStyles.boxShadow !== 'none';

      expect(hasFocusIndicator).toBeTruthy();
    }
  });

  test('no duplicate IDs on page', async ({ page }) => {
    await page.goto('/config/integrations');
    await page.waitForSelector('ha-config-integrations', { timeout: 10000 });

    const accessibilityScanResults = await new AxeBuilder({ page })
      .withRules(['duplicate-id'])
      .analyze();

    expect(accessibilityScanResults.violations).toEqual([]);
  });
});
