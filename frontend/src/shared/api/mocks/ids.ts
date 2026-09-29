export function randomHex(length = 32): string {
  const bytes = crypto.getRandomValues(new Uint8Array(Math.ceil(length / 2)))
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0'))
    .join('')
    .slice(0, length)
}

export function prefixedId(prefix: string): string {
  return `${prefix}_${randomHex()}`
}

export function nowIso(): string {
  return new Date().toISOString()
}

export function isoAfter(start: string, milliseconds: number): string {
  return new Date(Date.parse(start) + milliseconds).toISOString()
}

export async function sha256Hex(data: Uint8Array<ArrayBuffer> | string): Promise<string> {
  const bytes = typeof data === 'string' ? new TextEncoder().encode(data) : data
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}

export function rebrand<T>(value: T, replacements: ReadonlyMap<string, string>): T {
  let serialized = JSON.stringify(value)
  for (const [from, to] of replacements) {
    if (from !== to) serialized = serialized.split(from).join(to)
  }
  return JSON.parse(serialized) as T
}

export function clone<T>(value: T): T {
  return structuredClone(value)
}
