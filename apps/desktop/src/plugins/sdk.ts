/**
 * `@atlas/plugin-sdk`, the one module a plugin's screen imports from Atlas (step 16m): the app's live state to read, the server's
 * methods to call, and the pages and panels to open. A screen also imports `react` and
 * `react/jsx-runtime`, the app's own, and nothing else.
 *
 * It only grows, as the Python SDK: nothing is taken away or renamed.
 */
import { useStore } from '@nanostores/react'

import type { RpcMethod, RpcMethods } from '@/protocol/protocol.generated'
import { $connection, $home, $selected, current } from '@/store/app'
import { togglePanel } from '@/store/terminals'

import { $pluginPage } from './registry'

/** What this app offers screens: 1, panels and pages, the app's state, the server's methods. */
export const SCREEN_API = 1

/** Read a store and follow it, in a component: `useValue(host.state.conversation)`. */
export const useValue = useStore

export const host = {
  /** The app's live state, each a store to read with `useValue` or `.get()`. */
  state: {
    /** The connection to the home on screen: `{ state, error }`. */
    connection: $connection,
    /** The conversation on screen, or `null`. */
    conversation: $selected,
    /** The home on screen, by its name; `null` before the first answer. */
    home: $home
  },
  /** Call one of the server's methods, as the app does (docs: the protocol). */
  request<M extends RpcMethod>(method: M, params: RpcMethods[M]['params']): Promise<RpcMethods[M]['result']> {
    return current().client.call(method, params)
  },
  /** Show a plugin's page in the main area, by its key (`<plugin>:<id>`). */
  openPage(key: string): void {
    current().$view.set('plugin')
    $pluginPage.set(key)
  },
  /** Show or hide the side panel. */
  togglePanel(open?: boolean): void {
    togglePanel(open)
  }
}
