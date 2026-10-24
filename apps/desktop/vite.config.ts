import path from 'node:path'
import { fileURLToPath } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const here = path.dirname(fileURLToPath(import.meta.url))

export default defineConfig({
  // The window loads the build from file://, so every asset path stays relative.
  base: './',
  build: { emptyOutDir: true, outDir: 'out/renderer' },
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.join(here, 'src') } },
  server: { host: '127.0.0.1', port: 5174, strictPort: true }
})
