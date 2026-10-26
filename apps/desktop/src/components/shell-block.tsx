import { useStore } from '@nanostores/react'
import { createContext, useContext, useState } from 'react'
import { CodeBlock, CodeBlockCopyButton, type CustomRendererProps } from 'streamdown'

import { t } from '@/i18n'
import { $connection, checkCommand, runUserCommand } from '@/store/app'
import { $terminalAvailable } from '@/store/terminals'

/** The shells whose blocks get the ▶ (step 16f). */
export const SHELL_LANGUAGES = ['powershell', 'bash', 'sh', 'cmd', 'zsh']

/** Whether the answer the block is in still comes: a block cut halfway must not run. */
export const AnswerStreaming = createContext(false)

/**
 * A shell block of an answer, with Run (step 16f, asked for by the user on
 * 15/10/2026): the ▶ types the command into the conversation's terminal and presses Enter, each
 * line a command. It is off while the answer still comes. A command the trust gate finds dangerous
 * shows whole in a confirmation first (Atlas's own option); a
 * refusal runs nothing.
 */
export function ShellBlock({ code, isIncomplete, language }: CustomRendererProps) {
  const streaming = useContext(AnswerStreaming)
  const available = useStore($terminalAvailable)
  const { state } = useStore($connection)
  const [asking, setAsking] = useState<null | string>(null)
  const [busy, setBusy] = useState(false)
  const off = streaming || isIncomplete || available !== true || state !== 'open' || busy

  const run = () => {
    setBusy(true)
    void runUserCommand(code).finally(() => {
      setBusy(false)
    })
  }

  const play = () => {
    setBusy(true)
    void checkCommand(code)
      .then(found => {
        setBusy(false)

        if (found.dangerous) {
          setAsking(found.reason)
        } else {
          run()
        }
      })
      .catch(() => {
        setBusy(false)
      })
  }

  return (
    <div data-language={language} data-testid="shell-block">
      <CodeBlock code={code} isIncomplete={isIncomplete} language={language}>
        <CodeBlockCopyButton code={code} />
        <button
          aria-label={t.terminal.run}
          className="rounded px-1.5 text-sm text-foreground hover:bg-selected disabled:text-muted-foreground disabled:opacity-60"
          data-testid="run-block"
          disabled={off}
          onClick={play}
          title={available === false ? t.terminal.unavailable : t.terminal.run}
          type="button"
        >
          ▶
        </button>
      </CodeBlock>
      {asking !== null && (
        <div
          className="mt-2 space-y-2 rounded-lg border border-danger/40 bg-danger/10 p-3 text-sm"
          data-testid="run-confirm"
          role="alertdialog"
        >
          <p className="font-semibold">{t.terminal.dangerous}</p>
          {asking && <p className="text-xs text-soft">{asking}</p>}
          <pre className="overflow-x-auto rounded bg-raised p-2 font-mono text-xs whitespace-pre-wrap">{code}</pre>
          <div className="flex gap-2">
            <button
              className="rounded bg-danger px-3 py-1 text-xs font-medium text-white"
              onClick={() => {
                setAsking(null)
                run()
              }}
              type="button"
            >
              {t.terminal.runAnyway}
            </button>
            <button
              className="rounded border border-border-strong px-3 py-1 text-xs"
              onClick={() => {
                setAsking(null)
              }}
              type="button"
            >
              {t.terminal.cancel}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

/** Streamdown's renderers: the shells' blocks get the ▶. */
export const SHELL_RENDERERS = [{ component: ShellBlock, language: SHELL_LANGUAGES }]
