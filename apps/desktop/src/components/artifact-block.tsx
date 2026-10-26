import { CodeBlock, CodeBlockCopyButton, type CustomRendererProps } from 'streamdown'

import { t } from '@/i18n'
import { currentOwner } from '@/store/app'
import { openPreview } from '@/store/previews'

/** The blocks that open beside (step 16f): an artifact's languages. */
export const ARTIFACT_LANGUAGES = ['html', 'mermaid', 'markdown', 'md']

/**
 * A block of HTML, mermaid or Markdown of an answer, with "Open beside" (step 16f, decided with the
 * user on 15/10/2026): the preview never opens by itself, only on this
 * click, in a tab of the conversation's own. Not while the block still comes.
 */
export function ArtifactBlock({ code, isIncomplete, language }: CustomRendererProps) {
  const kind = language === 'md' ? 'markdown' : language

  return (
    <div data-language={kind} data-testid="artifact-block">
      <CodeBlock code={code} isIncomplete={isIncomplete} language={language}>
        <CodeBlockCopyButton code={code} />
        <button
          className="rounded px-1.5 text-xs text-soft hover:bg-selected disabled:opacity-50"
          data-testid="open-beside"
          disabled={isIncomplete}
          onClick={() => {
            openPreview(
              currentOwner(),
              { content: code, kind: 'artifact', label: title(kind, code), language: kind },
              true
            )
          }}
          type="button"
        >
          {t.preview.openBeside}
        </button>
      </CodeBlock>
    </div>
  )
}

/** A tab's name: an HTML page's title, else what it is. */
function title(kind: string, code: string): string {
  const named = /<title>([^<]{1,60})<\/title>/i.exec(code)?.[1]?.trim()

  return named || (kind === 'html' ? 'HTML' : kind === 'mermaid' ? 'Mermaid' : 'Markdown')
}
