import { useEffect, useState } from 'react'

/** The time now, read again every `every` ms while `active`: the trail's running timers. */
export function useNow(active: boolean, every = 1000): number {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!active) {
      return
    }

    const timer = setInterval(() => {
      setNow(Date.now())
    }, every)

    return () => {
      clearInterval(timer)
    }
  }, [active, every])

  return now
}

/** Seconds since `startedAt`, never below zero. */
export function elapsed(startedAt: null | number, now: number): null | number {
  return startedAt === null ? null : Math.max(0, (now - startedAt) / 1000)
}
