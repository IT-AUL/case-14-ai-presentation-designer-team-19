import { useSyncExternalStore } from 'react'
import type { JournalEntry, RepairBatch } from '@/entities/audit'
import { readJson, writeJson } from '@/shared/lib/storage'

const STORAGE_KEY = 'deckdna.audit-journal.v1'
const MAX_ENTRIES = 600

export interface VariantJournal {
  entries: JournalEntry[]
  batches: RepairBatch[]
}

type JournalState = Record<string, VariantJournal>

const EMPTY: VariantJournal = { entries: [], batches: [] }
const listeners = new Set<() => void>()
let snapshot: JournalState = readJson<JournalState>(STORAGE_KEY, {})

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function appendJournal(variantId: string, entries: readonly JournalEntry[], batch?: RepairBatch): void {
  const current = snapshot[variantId] ?? EMPTY
  const next: VariantJournal = {
    entries: [...current.entries, ...entries].slice(-MAX_ENTRIES),
    batches: batch ? [...current.batches, batch] : current.batches,
  }
  snapshot = { ...snapshot, [variantId]: next }
  writeJson(STORAGE_KEY, snapshot)
  listeners.forEach((listener) => listener())
}

export function readJournal(variantId: string): VariantJournal {
  return snapshot[variantId] ?? EMPTY
}

export function useVariantJournal(variantId: string): VariantJournal {
  return useSyncExternalStore(subscribe, () => readJournal(variantId))
}
