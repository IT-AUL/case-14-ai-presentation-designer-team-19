import { useSyncExternalStore } from 'react'
import { readJson, removeKey, writeJson } from '@/shared/lib/storage'
import type { ActiveProviderSession, ProviderSession, ProviderSessionTestResult } from './types'

const STORAGE_KEY = 'deckdna.provider-session.v1'

const listeners = new Set<() => void>()
let cached: ActiveProviderSession | null | undefined

function read(): ActiveProviderSession | null {
  if (cached === undefined) cached = readJson<ActiveProviderSession | null>(STORAGE_KEY, null, 'session')
  return cached
}

function write(next: ActiveProviderSession | null): void {
  cached = next
  if (next) writeJson(STORAGE_KEY, next, 'session')
  else removeKey(STORAGE_KEY, 'session')
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function isSessionExpired(session: Pick<ActiveProviderSession, 'expiresAt'>, now = Date.now()): boolean {
  const expiresAt = Date.parse(session.expiresAt)
  return Number.isFinite(expiresAt) && expiresAt <= now
}

export function getActiveProviderSession(): ActiveProviderSession | null {
  const session = read()
  return session && !isSessionExpired(session) ? session : null
}

export function setActiveProviderSession(session: ProviderSession): ActiveProviderSession {
  const active: ActiveProviderSession = {
    id: session.id,
    label: session.label,
    baseUrl: session.base_url,
    models: session.models,
    capabilities: session.capabilities,
    createdAt: session.created_at,
    expiresAt: session.expires_at,
    lastTest: null,
  }
  write(active)
  return active
}

export function recordProviderSessionTest(result: ProviderSessionTestResult): void {
  const current = read()
  if (current?.id === result.session_id) write({ ...current, lastTest: result })
}

export function clearActiveProviderSession(sessionId?: string): void {
  const current = read()
  if (!current || (sessionId && current.id !== sessionId)) return
  write(null)
}

export function useActiveProviderSession(): ActiveProviderSession | null {
  return useSyncExternalStore(subscribe, getActiveProviderSession, () => null)
}
