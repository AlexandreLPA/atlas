import { useEffect, useRef, useState } from 'react'

import { t } from '@/i18n'
import { answerSecret, type Card } from '@/store/app'

import { CardAnswering } from './card-answering'
import { AHEAD, CardFrame, FIELD, OUT } from './card-frame'

type SecretCardProps = { card: Extract<Card, { kind: 'secret' }> }

/** Step 16d: the agent asks for an API key by name. The card is masked, with an eye to show,
 * a Skip button, and a warning when a key is already stored. The value goes straight into the
 * vault through `answerSecret`; it is never kept in a store that outlives the card. */
export function SecretCard({ card }: SecretCardProps) {
  const [value, setValue] = useState('')
  const [visible, setVisible] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // The field takes the keyboard as the card appears, for the paste that follows, only when
  // nothing else has it: a key typed for the message box never lands in the key's field.
  useEffect(() => {
    if (document.activeElement === null || document.activeElement === document.body) {
      inputRef.current?.focus()
    }
  }, [])

  if (card.answered) {
    return <CardAnswering testId="secret-card" title={t.secret.title(card.params.name)} />
  }

  // The value goes exactly as pasted: no trim, no normalizing.
  const submit = () => {
    if (!value) {
      return
    }
    answerSecret(card.id, { value })
    // The field goes blank even on a stay (safety): the value is in the vault; keeping it in
    // the DOM would be enough to leak it on a crash dump.
    setValue('')
  }

  const skip = () => {
    answerSecret(card.id, { skipped: true })
    setValue('')
  }

  return (
    <CardFrame testId="secret-card" title={t.secret.title(card.params.name)}>
      <p className="text-soft">{card.params.reason}</p>
      {card.params.exists && (
        <p className="text-xs text-waiting-text" data-testid="secret-overwrite">
          {t.secret.overwrite(card.params.updated_at)}
        </p>
      )}
      {card.params.bot && <p className="text-xs text-muted-foreground">{t.secret.byBot(card.params.bot)}</p>}
      {!card.params.bot && <p className="text-xs text-muted-foreground">{t.secret.sharedWithBots}</p>}
      <div className="flex gap-2">
        <input
          aria-label={t.secret.title(card.params.name)}
          autoComplete="off"
          autoCorrect="off"
          className={`${FIELD} flex-1 font-mono`}
          data-testid="secret-input"
          onChange={event => {
            setValue(event.target.value)
          }}
          onKeyDown={event => {
            if (event.key === 'Enter') {
              event.preventDefault()
              submit()
            }
          }}
          placeholder={t.secret.placeholder}
          ref={inputRef}
          spellCheck={false}
          type={visible ? 'text' : 'password'}
          value={value}
        />
        <button
          aria-label={visible ? t.secret.hide : t.secret.show}
          className="rounded-md border border-border-strong bg-raised px-3 py-1.5 hover:bg-muted"
          data-testid="secret-eye"
          onClick={() => {
            setVisible(current => !current)
          }}
          type="button"
        >
          {visible ? t.secret.hide : t.secret.show}
        </button>
      </div>
      <div className="flex gap-2">
        <button
          className={AHEAD}
          data-testid="secret-save"
          disabled={value.length === 0}
          onClick={submit}
          type="button"
        >
          {t.secret.save}
        </button>
        <button className={OUT} data-testid="secret-skip" onClick={skip} type="button">
          {t.secret.skip}
        </button>
      </div>
    </CardFrame>
  )
}
