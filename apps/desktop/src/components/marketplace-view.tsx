import { useStore } from '@nanostores/react'
import { type ReactNode, useState } from 'react'
import { Streamdown } from 'streamdown'

import { t } from '@/i18n'
import type { MarketplacePage, MarketplaceRow, MarketplaceSecretRow } from '@/protocol/protocol.generated'
import {
  $market,
  actOnMarket,
  cancelMarketUpdate,
  closeMarketItem,
  openMarketItem,
  refreshMarketplace,
  setMarketKind,
  setMarketQuery,
  showConversation,
  storeMarketSecret
} from '@/store/app'
import { actionOf, counts, KINDS, type MarketKind, resolved, shown } from '@/store/marketplace'
import { openSettings } from '@/store/settings'

import { Bulb, Cpu, McpServer, Plug, Robot } from './ui/icons'

/**
 * The marketplace (step 16s): skills, MCP servers, plugins and executors made for Atlas, a tab
 * each, the Atlas team's first, and a page per item with its README at the commit that was
 * reviewed. The home's server reads the marketplace and installs; a plugin arrives off, and turning
 * it on is the consent of the Plugins screen. No downloads or ratings yet (decided by the user on
 * 24/10/2026).
 */
export function MarketplaceView() {
  const market = useStore($market)

  return (
    <div className="flex-1 overflow-y-auto" data-testid="marketplace-view">
      <div className="mx-auto max-w-5xl space-y-4 px-6 py-6">{market.open === null ? <Listing /> : <ItemPage />}</div>
    </div>
  )
}

function Listing() {
  const market = useStore($market)
  const { community, official } = shown(market.list, market.kind, market.query)
  const total = counts(market.list)
  const nothing = official.length + community.length === 0

  return (
    <>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-lg font-semibold">{t.marketplace.title}</h1>
          <p className="text-sm text-neutral-500">{t.marketplace.intro}</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <input
            aria-label={t.marketplace.search}
            className="w-56 rounded-md border border-neutral-300 bg-transparent px-2 py-1 text-sm dark:border-neutral-700"
            data-testid="marketplace-search"
            onChange={event => {
              setMarketQuery(event.target.value)
            }}
            placeholder={t.marketplace.search}
            type="search"
            value={market.query}
          />
          <button
            className={BUTTON}
            data-testid="marketplace-refresh"
            disabled={market.reading}
            onClick={refreshMarketplace}
            type="button"
          >
            {market.reading ? t.marketplace.reading : t.marketplace.refresh}
          </button>
          <button className={BUTTON} onClick={showConversation} type="button">
            {t.proposals.back}
          </button>
        </div>
      </div>
      <Notice />
      <div className="flex gap-1 border-b border-neutral-200 dark:border-neutral-800" role="tablist">
        {KINDS.map(kind => (
          <KindTab count={total[kind]} current={market.kind} key={kind} kind={kind} />
        ))}
      </div>
      {market.failed && <Failed text={market.failed} />}
      {nothing && (
        <p className="text-sm text-neutral-500">
          {market.list === null && market.reading
            ? t.marketplace.reading
            : market.query
              ? t.marketplace.noMatch
              : t.marketplace.empty}
        </p>
      )}
      <Shelf rows={official} title={t.marketplace.official} />
      <Shelf rows={community} title={t.marketplace.community} />
    </>
  )
}

/** Why the marketplace could not be read now: the last read stands in, or nothing does. */
function Notice() {
  const { error, list } = useStore($market)
  const problem = error ?? list?.problem

  if (!problem) {
    return null
  }

  const text = list?.read_at ? t.marketplace.stale(problem, when(list.read_at)) : t.marketplace.unread(problem)

  return (
    <p
      className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:bg-amber-950 dark:text-amber-100"
      data-testid="marketplace-notice"
      role="status"
    >
      {text}
    </p>
  )
}

function KindTab({ count, current, kind }: { count: number; current: MarketKind; kind: MarketKind }) {
  const on = current === kind

  return (
    <button
      aria-selected={on}
      className={`-mb-px border-b-2 px-3 py-1.5 text-sm ${
        on ? 'border-violet-600 font-medium' : 'border-transparent text-neutral-500 hover:text-neutral-800'
      }`}
      data-testid={`marketplace-tab-${kind}`}
      onClick={() => {
        setMarketKind(kind)
      }}
      role="tab"
      type="button"
    >
      {t.marketplace.tabs[kind]}
      <span className="ml-1.5 text-xs text-neutral-400">{count}</span>
    </button>
  )
}

function Shelf({ rows, title }: { rows: MarketplaceRow[]; title: string }) {
  if (rows.length === 0) {
    return null
  }

  return (
    <section className="space-y-2">
      <h2 className="text-sm font-medium text-neutral-500">{title}</h2>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
        {rows.map(row => (
          <Card key={row.name} row={row} />
        ))}
      </div>
    </section>
  )
}

function Card({ row }: { row: MarketplaceRow }) {
  return (
    <div
      className="flex cursor-pointer flex-col gap-2 rounded-lg border border-neutral-200 p-3 hover:border-violet-400 dark:border-neutral-800"
      data-name={row.name}
      data-testid="marketplace-card"
      onClick={() => {
        openMarketItem(row.name)
      }}
      onKeyDown={event => {
        if (event.key === 'Enter') {
          openMarketItem(row.name)
        }
      }}
      role="button"
      tabIndex={0}
    >
      <div className="flex items-start gap-3">
        <Picture row={row} size={40} />
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium">{row.title}</p>
          <p className="truncate text-xs text-neutral-500">{t.marketplace.by(row.author)}</p>
        </div>
        {row.tier === 'official' && <Badge>{t.marketplace.officialBadge}</Badge>}
      </div>
      <p className="line-clamp-2 min-h-10 text-sm text-neutral-600 dark:text-neutral-300">{row.summary}</p>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs text-neutral-400">{row.shipped ? t.marketplace.shipped : row.version}</span>
        <Action row={row} />
      </div>
    </div>
  )
}

function ItemPage() {
  const market = useStore($market)
  const page = market.page
  const row = page?.item ?? market.list?.items.find(item => item.name === market.open)

  return (
    <div className="space-y-4" data-testid="marketplace-page">
      <button className="text-sm text-violet-600 hover:underline" onClick={closeMarketItem} type="button">
        ← {t.marketplace.back}
      </button>
      {row && <Head row={row} />}
      {market.failed && <Failed text={market.failed} />}
      {market.brought.length > 0 && (
        <p className="text-sm text-neutral-600 dark:text-neutral-300">
          {t.marketplace.brought(market.brought.join(', '))}
        </p>
      )}
      {market.confirming !== null && <Confirm name={market.confirming} />}
      {row?.state === 'off' && <p className="text-sm text-neutral-500">{t.marketplace.turnOnHint}</p>}
      {market.pageError && <Failed text={market.pageError} />}
      {!page && !market.pageError && <p className="text-sm text-neutral-500">{t.marketplace.loadingPage}</p>}
      {page && <Body page={page} />}
    </div>
  )
}

function Head({ row }: { row: MarketplaceRow }) {
  const market = useStore($market)
  const [removing, setRemoving] = useState(false)
  const removable = !row.shipped && row.state !== 'available'

  return (
    <div className="flex flex-wrap items-start gap-4">
      <Picture row={row} size={64} />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold">{row.title}</h1>
          {row.tier === 'official' && <Badge>{t.marketplace.officialBadge}</Badge>}
          <span className="text-xs text-neutral-500">{t.marketplace.kinds[row.kind]}</span>
        </div>
        <p className="text-sm text-neutral-500">
          {[
            t.marketplace.by(row.author),
            row.shipped ? t.marketplace.shipped : '',
            row.version ? t.marketplace.version(row.version) : '',
            row.license ? t.marketplace.license(row.license) : ''
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
        <p className="text-sm">{row.summary}</p>
        {row.problem && <p className="text-sm text-amber-700 dark:text-amber-300">{row.problem}</p>}
        <div className="flex flex-wrap gap-3 text-sm">
          {row.source_url && (
            <a className="text-violet-600 hover:underline" href={row.source_url} rel="noreferrer" target="_blank">
              {row.kind === 'mcp' ? t.marketplace.homepage : t.marketplace.source}
            </a>
          )}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Action row={row} />
        {removable &&
          (removing ? (
            <button
              className={`${BUTTON} text-red-600`}
              data-testid="marketplace-remove-confirm"
              disabled={market.busy === row.name}
              onClick={() => {
                setRemoving(false)
                actOnMarket('remove', row.name)
              }}
              type="button"
            >
              {t.marketplace.confirmRemove}
            </button>
          ) : (
            <button
              className={BUTTON}
              data-testid="marketplace-remove"
              onClick={() => {
                setRemoving(true)
              }}
              type="button"
            >
              {t.marketplace.remove}
            </button>
          ))}
      </div>
    </div>
  )
}

function Body({ page }: { page: MarketplacePage }) {
  return (
    <div className="space-y-4">
      {page.needs.length > 0 && (
        <section className="space-y-1">
          <h2 className="text-sm font-medium">{t.marketplace.needs}</h2>
          <ul className="space-y-0.5 text-sm">
            {page.needs.map(need => (
              <li
                className={need.state === 'missing' ? 'text-amber-700 dark:text-amber-300' : 'text-neutral-600'}
                data-state={need.state}
                data-testid="marketplace-need"
                key={`${need.kind}-${need.name}`}
              >
                {t.marketplace.need[need.kind](need.name, need.state)}
              </li>
            ))}
          </ul>
        </section>
      )}
      {page.secrets.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">{t.marketplace.secrets}</h2>
          {page.secrets.map(secret => (
            <Secret item={page.item.name} key={secret.name} secret={secret} />
          ))}
        </section>
      )}
      {page.screenshots.length > 0 && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">{t.marketplace.screenshots}</h2>
          <div className="flex gap-3 overflow-x-auto">
            {page.screenshots.map(shot => (
              <img
                alt=""
                className="h-48 rounded-md border border-neutral-200 dark:border-neutral-800"
                key={shot}
                src={shot}
              />
            ))}
          </div>
        </section>
      )}
      {page.entry && (
        <section className="space-y-2">
          <h2 className="text-sm font-medium">{t.marketplace.entry}</h2>
          <pre
            className="overflow-x-auto rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-xs dark:border-neutral-800 dark:bg-neutral-900"
            data-testid="marketplace-entry-json"
          >
            {page.entry}
          </pre>
        </section>
      )}
      {page.readme && (
        <article
          className="rounded-lg border border-neutral-200 p-4 dark:border-neutral-800"
          data-testid="marketplace-readme"
        >
          {/* A stranger's text: its raw HTML is skipped, and its addresses kept to the item's tree. */}
          <Streamdown
            mode="static"
            skipHtml
            urlTransform={(url, key) => resolved(url, key === 'src' ? page.picture_base : page.link_base)}
          >
            {page.readme}
          </Streamdown>
        </article>
      )}
    </div>
  )
}

function Secret({ item, secret }: { item: string; secret: MarketplaceSecretRow }) {
  const [value, setValue] = useState('')
  // A stored secret is not asked for again: only a way to change it.
  const [changing, setChanging] = useState(false)

  return (
    <div className="text-sm" data-stored={secret.stored} data-testid="marketplace-secret">
      <p>
        <span className="font-mono">{secret.name}</span>
        <span className="ml-2 text-neutral-500">
          {secret.stored ? t.marketplace.secretStored : t.marketplace.secretMissing}
        </span>
        {secret.stored && !changing && (
          <button
            className="ml-2 text-violet-600 hover:underline"
            data-testid="marketplace-secret-change"
            onClick={() => {
              setChanging(true)
            }}
            type="button"
          >
            {t.plugins.changeSecret}
          </button>
        )}
      </p>
      {secret.description && (
        <p className="text-neutral-500">
          {secret.description}{' '}
          {secret.url && (
            <a className="text-violet-600 hover:underline" href={secret.url} rel="noreferrer" target="_blank">
              {secret.url}
            </a>
          )}
        </p>
      )}
      {(!secret.stored || changing) && (
        <form
          className="mt-1 flex gap-2"
          onSubmit={event => {
            event.preventDefault()
            void storeMarketSecret(item, secret.name, value).then(done => {
              if (done) {
                setValue('')
                setChanging(false)
              }
            })
          }}
        >
          <input
            aria-label={secret.name}
            autoComplete="off"
            className="w-72 rounded-md border border-neutral-300 bg-transparent px-2 py-1 dark:border-neutral-700"
            data-testid="marketplace-secret-input"
            onChange={event => {
              setValue(event.target.value)
            }}
            placeholder={t.marketplace.paste}
            spellCheck={false}
            type="password"
            value={value}
          />
          <button className={BUTTON} data-testid="marketplace-secret-save" disabled={!value.trim()} type="submit">
            {t.marketplace.save}
          </button>
        </form>
      )}
    </div>
  )
}

/** The item's button, by where it stands: install, update, turn on, or what it is. */
function Action({ row }: { row: MarketplaceRow }) {
  const market = useStore($market)
  const busy = market.busy === row.name
  const action = actionOf(row)
  const act = (run: () => void) => (event: { stopPropagation: () => void }) => {
    event.stopPropagation()
    run()
  }

  if (action === 'install' || action === 'update') {
    const label =
      action === 'install'
        ? busy
          ? t.marketplace.installing
          : t.marketplace.install
        : busy
          ? t.marketplace.updating
          : t.marketplace.update

    return (
      <button
        className={PRIMARY}
        data-action={action}
        data-testid="marketplace-action"
        disabled={market.busy !== null}
        onClick={act(() => {
          actOnMarket(action, row.name)
        })}
        type="button"
      >
        {label}
      </button>
    )
  }

  if (action === 'turn-on') {
    return (
      <button
        className={BUTTON}
        data-action={action}
        data-testid="marketplace-action"
        onClick={act(() => {
          openSettings('plugins')
        })}
        type="button"
      >
        {t.marketplace.turnOn}
      </button>
    )
  }

  const labels: Record<typeof action, string> = {
    installed: t.marketplace.installed,
    on: t.marketplace.on,
    retired: t.marketplace.retired,
    unavailable: t.marketplace.unavailable
  }

  return (
    <span
      className={`text-xs ${action === 'retired' || action === 'unavailable' ? 'text-amber-700' : 'text-neutral-500'}`}
      data-action={action}
      data-testid="marketplace-action"
      title={row.problem ?? undefined}
    >
      {labels[action]}
    </span>
  )
}

/** An update that would replace what the home changed: the user's word first. */
function Confirm({ name }: { name: string }) {
  return (
    <div
      className="space-y-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950"
      data-testid="marketplace-confirm"
    >
      <p>{t.marketplace.localChanges(name)}</p>
      <div className="flex gap-2">
        <button
          className={PRIMARY}
          data-testid="marketplace-update-anyway"
          onClick={() => {
            actOnMarket('update', name, true)
          }}
          type="button"
        >
          {t.marketplace.updateAnyway}
        </button>
        <button className={BUTTON} onClick={cancelMarketUpdate} type="button">
          {t.marketplace.cancel}
        </button>
      </div>
    </div>
  )
}

function Picture({ row, size }: { row: MarketplaceRow; size: number }) {
  if (row.image) {
    return (
      <img alt="" className="shrink-0 rounded-md object-cover" src={row.image} style={{ height: size, width: size }} />
    )
  }

  const Glyph = { executor: Cpu, mcp: McpServer, plugin: Plug, skill: Bulb }[row.kind]

  return (
    <span
      className="flex shrink-0 items-center justify-center rounded-md bg-neutral-100 text-neutral-500 dark:bg-neutral-800"
      style={{ height: size, width: size }}
    >
      {row.kind === 'executor' && row.shipped ? (
        <Robot aria-hidden size={size / 2} stroke={1.5} />
      ) : (
        <Glyph aria-hidden size={size / 2} stroke={1.5} />
      )}
    </span>
  )
}

function Badge({ children }: { children: ReactNode }) {
  return (
    <span className="shrink-0 rounded-full bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-700 dark:bg-violet-950 dark:text-violet-300">
      {children}
    </span>
  )
}

function Failed({ text }: { text: string }) {
  return (
    <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-950 dark:text-red-300" role="alert">
      {text}
    </p>
  )
}

const BUTTON =
  'rounded-md border border-neutral-300 px-2 py-1 text-sm hover:bg-neutral-100 disabled:opacity-40 dark:border-neutral-700 dark:hover:bg-neutral-800'
const PRIMARY =
  'rounded-md bg-violet-600 px-3 py-1 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-40'

/** A UTC time as the user's local date and time. */
function when(utc: string): string {
  return new Date(utc).toLocaleString(undefined, { dateStyle: 'short', timeStyle: 'short' })
}
