import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for Home Assistant E2E tests
 *
 * Tests assume a running Home Assistant instance with tuya-ble-mesh
 * integration installed. Set HA_BASE_URL environment variable to
 * point to your test instance (default: http://localhost:8123).
 */

export default defineConfig({
  testDir: './tests/e2e',

  // Auth setup: injects HA_TOKEN into browser localStorage before any test runs
  globalSetup: './tests/e2e/auth.setup.ts',

  // Timeout for each test
  timeout: 60 * 1000,

  // Test execution settings
  fullyParallel: false, // Run tests sequentially to avoid HA state conflicts
  forbidOnly: !!process.env.CI, // Fail on .only() in CI
  retries: process.env.CI ? 2 : 0, // Retry on CI
  workers: process.env.CI ? 1 : 1, // Single worker to avoid conflicts

  // Reporter configuration
  reporter: [
    ['html', { outputFolder: 'playwright-report' }],
    ['list'],
  ],

  // Shared settings for all tests
  use: {
    // Base URL for Home Assistant instance
    baseURL: process.env.HA_BASE_URL || 'http://localhost:8123',

    // Reuse authenticated browser state created by globalSetup
    storageState: './tests/e2e/storageState.json',

    // Capture screenshot on failure
    screenshot: 'only-on-failure',

    // Capture video on failure
    video: 'retain-on-failure',

    // Trace on failure for debugging
    trace: 'on-first-retry',

    // Viewport size
    viewport: { width: 1280, height: 720 },
  },

  // Configure projects for different browsers
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },

    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },

    {
      name: 'webkit',
      use: { ...devices['Desktop Safari'] },
    },

    // Mobile browsers
    {
      name: 'mobile-chrome',
      use: { ...devices['Pixel 5'] },
    },

    {
      name: 'mobile-safari',
      use: { ...devices['iPhone 13'] },
    },
  ],

  // Web server configuration (optional - for starting HA automatically)
  // Commented out by default - assumes HA is already running
  // webServer: {
  //   command: 'hass --script ensure_config && hass',
  //   url: 'http://localhost:8123',
  //   reuseExistingServer: !process.env.CI,
  //   timeout: 120 * 1000, // 2 minutes for HA to start
  // },
});
