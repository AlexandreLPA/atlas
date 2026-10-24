/**
 * The typed message catalog's machinery.
 *
 * `en` is whole, another language is a partial override of it, typed so an unknown key fails `tsc`, and merged over `en` so a missing key falls back.
 */

/** A partial language: every key optional, functions and strings replaced whole. */
export type Override<T> = T extends (...args: never[]) => string
  ? T
  : T extends string
    ? string
    : T extends object
      ? { [K in keyof T]?: Override<T[K]> }
      : T

const isPlainObject = (candidate: unknown): candidate is Record<string, unknown> =>
  candidate !== null && typeof candidate === 'object' && !Array.isArray(candidate)

/** A partial language over the base: records recurse, everything else is replaced. */
export function merge<T>(base: T, override: Override<T> | undefined): T {
  if (isPlainObject(base) && isPlainObject(override)) {
    const merged: Record<string, unknown> = { ...base }

    for (const key of Object.keys(override)) {
      const replacement = override[key]

      if (replacement === undefined) {
        continue
      }

      const below = merged[key]
      merged[key] =
        isPlainObject(below) && isPlainObject(replacement)
          ? merge(below, replacement as Override<typeof below>)
          : replacement
    }

    return merged as T
  }

  return (override ?? base) as T
}
