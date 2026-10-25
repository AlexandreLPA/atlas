/**
 * The plugins' screens follow the home on screen (step 16m): what its server's `plugin.screens`
 * lists is loaded, what leaves the list or changes is taken away or loaded again. A home that
 * switches loads its own plugins' screens in place of the other's.
 */
import { $screens } from '@/store/app'

import { loadedPowers, loadedScreens, loadedSource, loadScreen, unloadScreen } from './loader'

let syncing: Promise<void> = Promise.resolve()

/** Follow `$screens` from now on; returns what stops following, taking every screen away. */
export function startScreens(): () => void {
  const stop = $screens.subscribe(screens => {
    syncing = syncing.then(async () => {
      // A server before step 17b names no desktop powers.
      const wanted = new Map(screens.map(screen => [screen.plugin, screen]))

      for (const plugin of loadedScreens()) {
        if (!wanted.has(plugin)) {
          unloadScreen(plugin)
        }
      }

      for (const [plugin, { desktop = [], source }] of wanted) {
        if (loadedSource(plugin) !== source || loadedPowers(plugin) !== desktop.join(',')) {
          await loadScreen(plugin, source, desktop)
        }
      }
    })
  })

  return () => {
    stop()

    for (const plugin of loadedScreens()) {
      unloadScreen(plugin)
    }
  }
}
