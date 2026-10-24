import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { defineConfig } from 'vitest/config'

const here = path.dirname(fileURLToPath(import.meta.url))

// Two projects: the window's code in jsdom, the main process's in Node.
export default defineConfig({
  resolve: { alias: { '@': path.join(here, 'src') } },
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
