/**
 * The side panel's terminals (step 16f, decided with the user on 15/10/2026): a tab per
 * terminal, where a user command runs (the ▶ on a shell block, a message
 * starting with `!`) and its output goes back to the conversation as a terminal row.
 *
 * What the ▶ and the `!` share: the selected tab when it is free, another one
 * when it is not; the command waits for the terminal to be ready (src/terminal/terminal.ts); a
 * block of several lines runs each as a command, as a paste would; and a
 * command still running after 30 seconds is given as such, its end told later, when the agent
 * answers again (decided with the user on 15/10/2026).
 *
 * The shells are the server's, on its machine, reached over the connection of the home on screen
 * (step 15, src/terminal/connection.ts): the same here, on another machine and in the container.
 */
import { atom } from 'nanostores'

export interface TerminalTab {
  folder: string
  id: string
  /** Its shell writes marks: a command's end and exit code are known. */
  marks: boolean
  shell: string
}

/** What came of a user command, for its terminal row. */
export interface CommandOutcome {
  command: string
  exitCode: null | number
  output: string
  status: 'completed' | 'ended' | 'running' | 'unfinished'
}

/** Where a command's outcome goes: the conversation (a terminal row), or an executor's session. */
export type Deliver = (outcome: CommandOutcome) => Promise<void>

export const $panelOpen = atom(false)
export const $terminals = atom<TerminalTab[]>([])
export const $selectedTerminal = atom<null | string>(null)
/** The panel's tab on screen: a terminal's id, a preview's (store/previews.ts), or the executor's
 * (`EXECUTOR_TAB`). */
export const $panelTab = atom<null | string>(null)
/** The panel's tab with what the executor open in the sidebar has: its connectors. */
export const EXECUTOR_TAB = 'executor'
/** Whether the server's machine opens terminals; `null` until asked. */
export const $terminalAvailable = atom<boolean | null>(null)

/** Commands given as still running, by terminal: where their end goes. */
const late = new Map<string, { command: string; deliver: Deliver }>()
let listening = false
/** The terminals over the connection; none in the tests' window until one is given. */
let host: TerminalBridge | undefined

/** The terminals, for the view that shows one. */
export function terminalHost(): TerminalBridge | undefined {
  return host
}

function bridge(): TerminalBridge | undefined {
  return host
}

/** Where the terminals open: the app gives the connection's (src/terminal/connection.ts). */
export function setTerminals(given: TerminalBridge | undefined): void {
  host = given
  listening = false
  $terminalAvailable.set(null)
}

/** Ask the server on screen again: a connection opened, or another machine's is on screen. */
export function refreshTerminalAvailable(): Promise<boolean> {
  $terminalAvailable.set(null)

  return terminalAvailable()
}

/** Whether the server's machine opens terminals, asked once per connection. */
export async function terminalAvailable(): Promise<boolean> {
  const known = $terminalAvailable.get()

  if (known !== null) {
    return known
  }

  const found =
    (await bridge()
      ?.available()
      .catch(() => false)) ?? false
  $terminalAvailable.set(found)

  return found
}

export function togglePanel(open = !$panelOpen.get()): void {
  $panelOpen.set(open)
}

/** The executor's tab chosen, the panel left as it was: an executor was chosen in the sidebar.
 * Choosing one does not open the panel, nor leaving it close it: open or hidden, the panel stays
 * so from screen to screen (asked for by the user on 17/10/2026). */
export function showExecutorTab(): void {
  $panelTab.set(EXECUTOR_TAB)
}

/** The executor's tab gone, back to the executors' list: the panel goes back to a terminal. */
export function leaveExecutorTab(): void {
  if ($panelTab.get() === EXECUTOR_TAB) {
    $panelTab.set($selectedTerminal.get())
  }
}

/** A new terminal in `folder`, selected, the panel open. */
export async function openTerminal(folder: string): Promise<TerminalTab> {
  const terminal = bridge()

  if (!terminal) {
    throw new Error('The terminal is unavailable here.')
  }

  listen(terminal)
  // About the panel's size: the shell draws its first lines before the view measures it, and a
  // line drawn wider than the view wraps.
  const started = await terminal.start({ cols: 72, cwd: folder, rows: 40 })
  const tab: TerminalTab = { folder, id: started.id, marks: started.marks, shell: started.shell }
  $terminals.set([...$terminals.get(), tab])
  $selectedTerminal.set(tab.id)
  $panelTab.set(tab.id)
  $panelOpen.set(true)

  return tab
}

export function closeTerminal(id: string): void {
  void bridge()?.dispose(id)
  late.delete(id)
  const left = $terminals.get().filter(tab => tab.id !== id)
  $terminals.set(left)

  if ($selectedTerminal.get() === id) {
    $selectedTerminal.set(left.at(-1)?.id ?? null)
  }
}

export function selectTerminal(id: string): void {
  $selectedTerminal.set(id)
  $panelTab.set(id)
}

/**
 * Run a user command in a terminal in `folder` and hand what came of it to `deliver`. Each line of
 * a block runs as a command; the outcome is the block's, its output each line's in turn.
 */
export async function runCommand(command: string, folder: string, deliver: Deliver): Promise<void> {
  const terminal = bridge()
  const lines = command
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(Boolean)

  if (!terminal || lines.length === 0) {
    return
  }

  $panelOpen.set(true)
  const tab = await freeTab(terminal, folder)
  const outputs: string[] = []
  let outcome: CommandOutcome = { command: lines.join('\n'), exitCode: null, output: '', status: 'completed' }

  for (const line of lines) {
    const ran = await terminal.run(tab.id, line).catch((error: unknown): TerminalRun => ({
      exitCode: null,
      output: error instanceof Error ? error.message : String(error),
      status: 'unfinished'
    }))
    outputs.push(lines.length > 1 ? `$ ${line}\n${ran.output}`.trim() : ran.output)
    outcome = { ...outcome, exitCode: ran.exitCode, status: ran.status }

    if (ran.status !== 'completed') {
      break
    }
  }

  outcome = { ...outcome, output: outputs.join('\n\n') }

  if (outcome.status === 'running') {
    late.set(tab.id, { command: outcome.command, deliver })
  }

  await deliver(outcome)
}

/** The selected terminal when it is free, else a new one in `folder`. */
async function freeTab(terminal: TerminalBridge, folder: string): Promise<TerminalTab> {
  const selected = $terminals.get().find(tab => tab.id === $selectedTerminal.get())

  if (selected && !(await terminal.busy(selected.id).catch(() => true))) {
    return selected
  }

  return await openTerminal(folder)
}

function listen(terminal: TerminalBridge): void {
  if (listening) {
    return
  }

  listening = true
  terminal.onEnded((id, end) => {
    const waiting = late.get(id)

    if (waiting) {
      late.delete(id)
      void waiting.deliver({ command: waiting.command, exitCode: end.exitCode, output: end.output, status: 'ended' })
    }
  })
  terminal.onExit(id => {
    closeTerminal(id)
  })
}
