/**
 * The installation's own programs (docs/ARCHITECTURE.md, The installer): uv, Node, `gh` and, on
 * Windows, Git, pinned by the installer and named in `tools/toolchain.json` beside the checkout. The
 * app runs its server with that uv, and puts their folders first on the server's PATH, never on the
 * user's. A development checkout has no such file, and the PATH serves.
 *
 * The rules are `atlas.installation.model.parse_toolchain`'s, and a damaged file is refused the same
 * way: the installer repairs it, nothing guesses around it.
 */
import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'

export const TOOLS_FOLDER = 'tools'
export const TOOLCHAIN_FILE = 'toolchain.json'
const VERSION = 1
const PROGRAM_NAME = /^[a-z][a-z0-9-]*$/

/** The programs a toolchain file names, each a relative path with `/` under the tools folder. */
export function parseToolchain(text: string): Record<string, string> {
  const broken = `The installation's ${TOOLCHAIN_FILE} is damaged; run the installer again to repair it`
  let found: unknown

  try {
    found = JSON.parse(text)
  } catch (error) {
    throw new Error(`${broken} (${error instanceof Error ? error.message : String(error)}).`, { cause: error })
  }

  if (typeof found !== 'object' || found === null || Array.isArray(found)) {
    throw new Error(`${broken} (it is not an object).`)
  }

  const data = found as Record<string, unknown>

  if (data.version !== VERSION) {
    throw new Error(`${broken} (version ${JSON.stringify(data.version)}; this Atlas reads ${VERSION}).`)
  }

  const listed = data.programs

  if (typeof listed !== 'object' || listed === null || Array.isArray(listed)) {
    throw new Error(`${broken} (no programs).`)
  }

  const programs: Record<string, string> = {}

  for (const [name, where] of Object.entries(listed as Record<string, unknown>)) {
    if (!PROGRAM_NAME.test(name)) {
      throw new Error(`${broken} (a program named ${JSON.stringify(name)}).`)
    }

    if (typeof where !== 'string' || !where || where.includes('\\')) {
      throw new Error(`${broken} (${name}'s path ${JSON.stringify(where)}).`)
    }

    if (where.startsWith('/') || where.includes(':') || where.split('/').includes('..')) {
      throw new Error(`${broken} (${name}'s path ${JSON.stringify(where)} leaves the tools folder).`)
    }

    programs[name] = where
  }

  return programs
}

/**
 * The programs beside the checkout at `root`, by name, as full paths; none when the installer left
 * no file there.
 */
export function readToolchain(root: string): Record<string, string> {
  const folder = path.join(path.dirname(root), TOOLS_FOLDER)
  const file = path.join(folder, TOOLCHAIN_FILE)

  if (!existsSync(file)) {
    return {}
  }

  const programs: Record<string, string> = {}

  for (const [name, where] of Object.entries(parseToolchain(readFileSync(file, 'utf8')))) {
    const program = path.join(folder, ...where.split('/'))

    if (!existsSync(program)) {
      throw new Error(`The installation's ${name} is missing (${program}); run the installer again to repair it.`)
    }

    programs[name] = program
  }

  return programs
}

/** The folders the programs sit in, in the file's order, each once. */
export function toolchainFolders(programs: Record<string, string>): string[] {
  return [...new Set(Object.values(programs).map(program => path.dirname(program)))]
}

/**
 * `env` with `folders` first on its PATH. On Windows the variable is `Path`, and a second `PATH`
 * beside it would leave which one the child reads to chance, so the one already there is replaced.
 */
export function withPath(env: NodeJS.ProcessEnv, folders: string[]): NodeJS.ProcessEnv {
  if (folders.length === 0) {
    return env
  }

  const key = Object.keys(env).find(name => name.toUpperCase() === 'PATH') ?? 'PATH'
  const rest = env[key]
  const result = { ...env }
  result[key] = [...folders, ...(rest ? [rest] : [])].join(path.delimiter)

  return result
}
