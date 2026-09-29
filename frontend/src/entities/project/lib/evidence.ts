import { useSyncExternalStore } from 'react'
import { readJson, writeJson } from '@/shared/lib/storage'

const STORAGE_KEY = 'deckdna.evidence.v1'

export type EvidenceMark = 'generated' | 'repaired' | 'exported' | 'reaudited'

type EvidenceState = Record<string, Partial<Record<EvidenceMark, true>>>

const listeners = new Set<() => void>()
let snapshot: EvidenceState = readJson<EvidenceState>(STORAGE_KEY, {})

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function markEvidence(projectId: string, mark: EvidenceMark): void {
  if (snapshot[projectId]?.[mark]) return
  snapshot = { ...snapshot, [projectId]: { ...snapshot[projectId], [mark]: true } }
  writeJson(STORAGE_KEY, snapshot)
  listeners.forEach((listener) => listener())
}

export function resetEvidence(projectId: string): void {
  if (!snapshot[projectId]) return
  snapshot = Object.fromEntries(Object.entries(snapshot).filter(([id]) => id !== projectId))
  writeJson(STORAGE_KEY, snapshot)
  listeners.forEach((listener) => listener())
}

const EMPTY: Partial<Record<EvidenceMark, true>> = {}

export function useProjectEvidence(projectId: string | undefined): Partial<Record<EvidenceMark, true>> {
  return useSyncExternalStore(subscribe, () => (projectId ? (snapshot[projectId] ?? EMPTY) : EMPTY))
}
