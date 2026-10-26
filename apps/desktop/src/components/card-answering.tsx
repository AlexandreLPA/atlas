import { t } from '@/i18n'

/** A card the user answered, until the server has the answer: what it asked, and that the answer
 * is on its way, never a card gone as if it had arrived. */
export function CardAnswering({ testId, title }: { testId: string; title: string }) {
  return (
    <section
      aria-busy="true"
      aria-label={title}
      className="rounded-lg border border-border p-4 text-sm text-muted-foreground"
      data-answered="true"
      data-testid={testId}
    >
      <p className="font-medium text-soft">{title}</p>
      <p className="mt-1" role="status">
        {t.card.answering}
      </p>
    </section>
  )
}
