const stampFormat = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

export const PROJECT_NAME_MAX = 120

export function defaultProjectName(prefix: string, now: Date = new Date()): string {
  return `${prefix} · ${stampFormat.format(now)}`
}

export function normalizeProjectName(value: string): string {
  return value.replace(/\s+/g, ' ').trim().slice(0, PROJECT_NAME_MAX)
}
