/**
 * What the main process knows of the homes (step 10c, docs/ARCHITECTURE.md, Homes in the app): the
 * name the backup's save dialog offers, and the home the app opens on.
 *
 * **The app opens its last home**, the last one it showed, kept apart from the terminal's. It is
 * kept in the app's own folder; the terminal still opens the active home (`atlas home use`), and
 * so does the app until it has shown one.
 *
 * A last home that no longer exists (deleted or renamed in the terminal) gives way to the active
 * home: starting a server on a home that is gone would fail before the window opens. An
 * `ATLAS_HOME` inside `homes/` names one home itself, and the app opens that one.
 */
import { mkdirSync, readFileSync, renameSync, statSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

/** The default home's name, and what a named home may be called (`atlas.home.model`). */
const DEFAULT = 'default'
const NAME = /^[a-z0-9][a-z0-9_-]{0,63}$/

const isName = (name: unknown): name is string => typeof name === 'string' && (name === DEFAULT || NAME.test(name))

/**
 * The folder the homes live in: `ATLAS_HOME`, or `~/.atlas`, as `atlas.home.resolve_home` finds
 * it; `null` when `ATLAS_HOME` is one named home, which then opens whatever was last shown.
 */
export function homesRoot(env: NodeJS.ProcessEnv, userHome = os.homedir()): null | string {
  const declared = env.ATLAS_HOME?.trim() || path.join(userHome, '.atlas')

  return path.basename(path.dirname(declared)) === 'homes' ? null : declared
}

/** The last home shown, while it still exists under `root`; `null`, and the active home opens. */
export function lastHome(file: string, root: null | string): null | string {
  if (root === null) {
    return null
  }

  let name: unknown

  try {
    name = (JSON.parse(readFileSync(file, 'utf8')) as { home?: unknown }).home
  } catch {
    return null
  }

  if (!isName(name)) {
    return null
  }

  if (name === DEFAULT) {
    return name
  }

  try {
    return statSync(path.join(root, 'homes', name)).isDirectory() ? name : null
  } catch {
    return null
  }
}

/** Keep the home on screen as the one to open next time; written whole or not at all. */
export function rememberHome(file: string, name: string): void {
  if (!isName(name)) {
    return
  }

  mkdirSync(path.dirname(file), { recursive: true })
  const partial = `${file}.${String(process.pid)}.partial`
  writeFileSync(partial, JSON.stringify({ home: name }))
  renameSync(partial, file)
}

/** `atlas-backup-<home>-<UTC>.zip`, the name `atlas backup` gives (`atlas.home.backup_name`). */
export function backupName(home: string, now: Date): string {
  const stamp = now
    .toISOString()
    .replace(/[-:]/g, '')
    .replace(/\.\d+Z$/, 'Z')

  return `atlas-backup-${home}-${stamp}.zip`
}
