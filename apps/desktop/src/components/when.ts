/** When a row of the sidebar last changed: the time today, the date before. */
export function when(utc: null | string | undefined): string {
  if (!utc) {
    return ''
  }

  const moment = new Date(utc)
  const today = new Date().toDateString() === moment.toDateString()

  return today
    ? moment.toLocaleTimeString(undefined, { timeStyle: 'short' })
    : moment.toLocaleDateString(undefined, { dateStyle: 'short' })
}
