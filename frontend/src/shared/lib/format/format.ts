const numberFormat = new Intl.NumberFormat('ru-RU')

export function formatNumber(value: number): string {
  return numberFormat.format(value)
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`
  return `${(bytes / (1024 * 1024)).toFixed(1).replace('.', ',')} МБ`
}

export function formatClock(seconds: number): string {
  const safe = Math.max(0, Math.floor(seconds))
  return `${Math.floor(safe / 60)}:${String(safe % 60).padStart(2, '0')}`
}

export function formatSeconds(seconds: number): string {
  if (seconds < 10) return `${seconds.toFixed(1).replace('.', ',')} с`
  if (seconds < 60) return `${Math.round(seconds)} с`
  return formatClock(seconds)
}

export function formatShortHash(hash: string): string {
  return hash.length > 12 ? `${hash.slice(0, 4)}…${hash.slice(-4)}` : hash
}

export function formatPercent(fraction: number, digits = 0): string {
  return `${(fraction * 100).toFixed(digits)}%`
}

export function formatIndex(index: number): string {
  return String(index).padStart(2, '0')
}

const dateTimeFormat = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

export function formatDateTime(iso: string): string {
  return dateTimeFormat.format(new Date(iso))
}

export function pluralize(count: number, forms: readonly [string, string, string]): string {
  const mod10 = count % 10
  const mod100 = count % 100
  if (mod10 === 1 && mod100 !== 11) return forms[0]
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1]
  return forms[2]
}
