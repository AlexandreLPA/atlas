import { defineConfig } from '@playwright/test'

/** The proof against a real model (`scripts/prove_desktop.py`), kept out of the e2e suite. */
export default defineConfig({
  reporter: [['list']],
  retries: 0,
  testDir: '.',
  testMatch: '*.prove.ts',
  timeout: 45 * 60_000,
  use: { screenshot: 'only-on-failure', trace: 'retain-on-failure' },
  workers: 1
})
