import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vitest/config'

const here = path.dirname(fileURLToPath(import.meta.url))

// Two projects: the window's code in jsdom, the main process's in Node.
export default defineConfig({
  // `@atlas/plugin-sdk` as the loader gives it to a plugin's screen, so a shipped plugin's screen is
  // tested as it runs (the voice plugin's, step 17a).
  resolve: {
    alias: { '@': path.join(here, 'src'), '@atlas/plugin-sdk': path.join(here, 'src', 'plugins', 'sdk.ts') },
    // Its `react` is the app's, as the loader's shims make it, though the file sits outside the app.
    dedupe: ['react']
  },
  // The repository is read too: the plugin guide, src/atlas/docs/pages/extending/plugins.md, that a test keeps whole.
  server: { fs: { allow: [path.join(here, '..', '..')] } },
  test: {
    projects: [
      {
        extends: true,
        test: {
          environment: 'jsdom',
          include: ['src/**/*.test.{ts,tsx}'],
          name: 'window',
          // The first test of a file pays jsdom's start, past the 5 s default on CI.
          testTimeout: 15_000
        }
      },
      {
        extends: true,
        test: { environment: 'node', include: ['electron/**/*.test.ts'], name: 'electron', testTimeout: 30_000 }
      }
    ]
  }
})
