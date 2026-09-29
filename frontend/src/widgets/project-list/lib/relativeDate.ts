const timeFormat = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })
const dayFormat = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' })
const yearFormat = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' })

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

const DAY_MS = 24 * 60 * 60 * 1000

export function formatRelativeDate(iso: string, now: Date = new Date()): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const days = Math.round((startOfDay(now) - startOfDay(date)) / DAY_MS)
  const time = timeFormat.format(date)
  if (days === 0) return `сегодня, ${time}`
  if (days === 1) return `вчера, ${time}`
  if (date.getFullYear() !== now.getFullYear()) return yearFormat.format(date)
  return `${dayFormat.format(date)}, ${time}`
}
