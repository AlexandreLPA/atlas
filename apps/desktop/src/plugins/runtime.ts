/**
 * The plugins' screens follow the home on screen (step 16m): what its server's `plugin.screens`
 * lists is loaded, what leaves the list or changes is taken away or loaded again. A home that
 * switches loads its own plugins' screens in place of the other's.
 */
import { $screens } from '@/store/app'

import { loadedScreens, loadedSource, loadScreen, unloadScreen } from './loader'

let syncing: Promise<void> = Promise.resolve()

/** Follow `$screens` from now on; returns what stops following, taking every screen away. */
export function startScreens(): () => void {
  const stop = $screens.subscribe(screens => {
    syncing = syncing.then(async () => {
      const wanted = new Map(screens.map(screen => [screen.plugin, screen.source]))

      for (const plugin of loadedScreens()) {
        if (!wanted.has(plugin)) {
          unloadScreen(plugin)
        }
      }

      for (const [plugin, source] of wanted) {
        if (loadedSource(plugin) !== source) {
          await loadScreen(plugin, source)
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
