import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'list',

  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:9456',
    trace: 'on-first-retry',
    headless: true,
  },

  projects: [
    {
      name: 'firefox',
      testIgnore: '**/mobile-*.spec.ts',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'mobile',
      testMatch: '**/mobile-*.spec.ts',
      use: {
        viewport: { width: 375, height: 812 },
        isMobile: true,
        hasTouch: true,
      },
    },
  ],
});
