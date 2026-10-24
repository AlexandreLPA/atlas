// `npm run dev`: the window from Vite's dev server (it reloads on save), the main process bundled
// once, and Electron pointed at the dev server.
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import { createServer } from 'vite'

const url = 'http://127.0.0.1:5174'
const vite = await createServer({ configFile: 'vite.config.ts' })
await vite.listen()
await import('./build-electron.mjs')

const electron = createRequire(import.meta.url)('electron')
// An editor built on Electron (VS Code) sets this for its children; it would start the app as Node.
const env = { ...process.env, ATLAS_DESKTOP_DEV_SERVER: url }
delete env.ELECTRON_RUN_AS_NODE
const child = spawn(electron, ['.'], { env, stdio: 'inherit' })

child.on('exit', code => {
  void vite.close().then(() => process.exit(code ?? 0))
})
