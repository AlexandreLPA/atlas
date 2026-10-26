import { useEffect, useState } from 'react'

/**
 * How long a read may take before its place shows it is being read: a read under it shows
 * nothing at all, so a quick one never flashes (no indicator under a second, the usability
 * studies say; a skeleton for a page that takes up to ten).
 */
export const SKELETON_DELAY_MS = 400

/** `true` once `ms` passed since it first showed. */
export function useDelayed(ms = SKELETON_DELAY_MS): boolean {
  const [shown, setShown] = useState(ms <= 0)

  useEffect(() => {
    if (ms <= 0) {
      return undefined
    }

    const timer = window.setTimeout(() => {
      setShown(true)
    }, ms)

    return () => {
      window.clearTimeout(timer)
    }
  }, [ms])

  return shown
}

/** A list being read: rows the shape of its own, never "nothing here" before it was read. */
export function SkeletonRows({ count = 4, label }: { count?: number; label: string }) {
  const shown = useDelayed()

  return (
    <div aria-busy="true" aria-label={label} className="space-y-1 px-2 py-1" data-testid="skeleton" role="status">
      {shown &&
        Array.from({ length: count }, (_, index) => (
          <div className="flex items-center gap-2 px-2 py-2" key={index}>
            <span
              className="h-2.5 rounded-sm bg-neutral-200 motion-safe:animate-pulse dark:bg-neutral-800"
              style={{ width: `${String(55 + ((index * 23) % 35))}%` }}
            />
          </div>
        ))}
    </div>
  )
}

/** A conversation being read: a message and an answer the shape of its own. */
export function SkeletonThread({ label }: { label: string }) {
  const shown = useDelayed()

  return (
    <div
      aria-busy="true"
      aria-label={label}
      className="mx-auto w-full max-w-3xl space-y-4 px-6 py-6"
      data-testid="skeleton"
      role="status"
    >
      {shown && (
        <>
          <div className="ml-auto h-9 w-2/5 rounded-2xl bg-neutral-100 motion-safe:animate-pulse dark:bg-neutral-800" />
          {[90, 75, 82, 40].map(width => (
            <div
              className="h-3 rounded-sm bg-neutral-100 motion-safe:animate-pulse dark:bg-neutral-800"
              key={width}
              style={{ width: `${String(width)}%` }}
            />
          ))}
        </>
      )}
    </div>
  )
}
