import { t } from '@/i18n'
import { type Readable, readAgain } from '@/store/app'

import { InlineError } from './ui/inline-error'

/** A list or a page whose read failed, in its own place: why, and reading it again. */
export function ReadFailed({ error, what }: { error: string; what: Readable }) {
  return (
    <InlineError
      action={
        <button
          className="shrink-0 font-medium underline-offset-2 hover:underline"
          data-testid="read-again"
          onClick={() => {
            readAgain(what)
          }}
          type="button"
        >
          {t.lists.again}
        </button>
      }
      testId="read-failed"
    >
      {t.lists.failed(error)}
    </InlineError>
  )
}
