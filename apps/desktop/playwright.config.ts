import { defineConfig } from '@playwright/test'

/**
 * End to end: the real app (Electron), the real `atlas serve`, and the fake model server. One
 * worker, so each test owns its app.
 */
export default defineConfig({
  forbidOnly: Boolean(process.env.CI),
  fullyParallel: false,
  reporter: [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  retries: 0,
  testDir: './e2e',
  timeout: 120_000,
  use: { screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  workers: 1
})
