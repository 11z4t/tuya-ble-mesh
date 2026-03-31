import { chromium, FullConfig } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const STORAGE_STATE = path.join(__dirname, 'storageState.json');

/**
 * Global setup: inject HA long-lived access token into browser localStorage
 * so all E2E tests start pre-authenticated.
 *
 * Requires: HA_TOKEN env var (long-lived access token from HA UI or SSH)
 * Reads:    HA_BASE_URL env var (default: http://localhost:8123)
 *
 * If HA_TOKEN is not set, creates an empty storageState so Playwright
 * doesn't fail on startup — tests will redirect to login page.
 */
async function globalSetup(_config: FullConfig): Promise<void> {
  const haToken = process.env.HA_TOKEN ?? '';
  const haBaseUrl = process.env.HA_BASE_URL ?? 'http://localhost:8123';

  if (!haToken) {
    fs.writeFileSync(STORAGE_STATE, JSON.stringify({ cookies: [], origins: [] }));
    console.log('[auth.setup] HA_TOKEN not set — tests will hit HA login page');
    return;
  }

  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();

  try {
    // Navigate to HA and wait for it to settle (including auth redirect).
    // HA maintains WebSocket connections so 'networkidle' never fires — use 'load'.
    // After load, HA's SPA will redirect to /auth/authorize if not logged in.
    await page.goto(haBaseUrl, { waitUntil: 'load', timeout: 20000 });
    await page.waitForTimeout(2000); // Give HA time to run its client-side routing

    // Inject the long-lived token as hassTokens in localStorage.
    // localStorage is shared across the whole origin, so writing here
    // (even on /auth/authorize) makes it available when we reload.
    await page.evaluate(
      ([url, token]: [string, string]) => {
        const hassTokens = JSON.stringify({
          access_token: token,
          token_type: 'Bearer',
          expires_in: 31536000,
          expires_at: Date.now() / 1000 + 31536000, // ~1 year from now
          hassUrl: url,
          clientId: url + '/',
          state: null,
          refresh_token: '',
        });
        localStorage.setItem('hassTokens', hassTokens);
      },
      [haBaseUrl, haToken] as [string, string],
    );

    // Reload so HA picks up the injected auth
    await page.goto(haBaseUrl, { waitUntil: 'load', timeout: 20000 });
    await page.waitForTimeout(2000);

    // Verify we're authenticated
    try {
      await page.waitForSelector('home-assistant', { timeout: 20000 });
    } catch {
      const url = page.url();
      if (url.includes('/auth/')) {
        throw new Error(`Auth injection failed — still on login page: ${url}`);
      }
      // May have loaded without the selector being immediately visible — continue
    }

    await context.storageState({ path: STORAGE_STATE });
    console.log(`[auth.setup] Authenticated storageState saved: ${STORAGE_STATE}`);
  } finally {
    await browser.close();
  }
}

export default globalSetup;
