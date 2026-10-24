/**
 * A plugin's contribution on screen (step 16m): rendered inside an error boundary of its own, so
 * a screen that throws shows why in its place and the app goes on.
 */
import { useStore } from '@nanostores/react'
import { Component, type ErrorInfo, type ReactNode } from 'react'

import { t } from '@/i18n'

import { $contributions, $pluginPage, type Registered } from './registry'

interface BoundaryState {
  error: Error | null
}

class Boundary extends Component<{ children: ReactNode; name: string }, BoundaryState> {
  override state: BoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): BoundaryState {
    return { error }
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`The plugin screen ${this.props.name} failed.`, error, info.componentStack)
  }

  override render(): ReactNode {
    if (this.state.error) {
      return (
        <p className="p-3 text-sm text-red-700 dark:text-red-300" data-testid="plugin-failed" role="alert">
          {t.plugins.screenFailed(this.props.name, this.state.error.message)}
        </p>
      )
    }

    return this.props.children
  }
}

/** One contribution, its errors kept to itself: its `render` runs inside the boundary, where a
 *  throw is caught, never in the component that holds it. */
export function ContributionView({ item }: { item: Registered }) {
  return (
    <Boundary key={item.key} name={item.key}>
      <Rendered item={item} />
    </Boundary>
  )
}

function Rendered({ item }: { item: Registered }) {
  return <>{item.render()}</>
}

/** The plugin page the main area shows. */
export function PluginPage() {
  const key = useStore($pluginPage)
  const item = useStore($contributions).find(found => found.key === key && found.area === 'page')

  if (!item) {
    return <p className="p-6 text-sm text-neutral-500">{t.plugins.pageGone}</p>
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto" data-testid="plugin-page">
      <ContributionView item={item} />
    </div>
  )
}
