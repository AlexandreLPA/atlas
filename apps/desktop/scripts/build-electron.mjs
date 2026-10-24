// Bundle the main process (ESM) and the preload (CommonJS: a sandboxed preload cannot be a module)
// into out/, beside the window's build. Type checking is tsc's job; esbuild only strips and bundles.
import { build } from 'esbuild'

const common = {
  bundle: true,
  external: ['electron'],
  logLevel: 'warning',
  platform: 'node',
  target: 'node22'
}

await build({ ...common, entryPoints: ['electron/main.ts'], format: 'esm', outfile: 'out/main.mjs' })
await build({ ...common, entryPoints: ['electron/preload.ts'], format: 'cjs', outfile: 'out/preload.cjs' })
