function resolve(kind: 'local' | 'session'): Storage | null {
  try {
    return kind === 'local' ? window.localStorage : window.sessionStorage
  } catch {
    return null
  }
}

export function readJson<T>(key: string, fallback: T, kind: 'local' | 'session' = 'local'): T {
  const raw = resolve(kind)?.getItem(key)
  if (!raw) return fallback
  try {
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

export function writeJson(key: string, value: unknown, kind: 'local' | 'session' = 'local'): void {
  try {
    resolve(kind)?.setItem(key, JSON.stringify(value))
  } catch {
    return
  }
}

export function removeKey(key: string, kind: 'local' | 'session' = 'local'): void {
  resolve(kind)?.removeItem(key)
}
