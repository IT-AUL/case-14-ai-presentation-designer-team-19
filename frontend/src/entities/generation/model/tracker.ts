import { readJson, writeJson } from '@/shared/lib/storage'
import { submitGeneration, submitRetry } from '../api/requests'
import type { GenerationAccepted, GenerationCreate } from './types'

export type SubmissionStatus = 'pending' | 'accepted' | 'rejected'

export interface TrackedGeneration {
  trackingId: string
  kind: 'create' | 'retry'
  projectId: string | null
  parentGenerationId: string | null
  startedAt: number
  settledAt: number | null
  status: SubmissionStatus
  accepted: GenerationAccepted | null
  error: Error | null
}

interface StoredTrackedGeneration {
  trackingId: string
  kind: 'create' | 'retry'
  projectId: string | null
  parentGenerationId: string | null
  startedAt: number
  settledAt: number | null
  accepted: GenerationAccepted
}

const TRACKING_PREFIX = 'local-'
const STORAGE_KEY = 'deckdna.tracked-runs.v1'
const MAX_STORED_RUNS = 30

const runs = new Map<string, TrackedGeneration>()
const byGenerationId = new Map<string, string>()
const submissions = new Map<string, Promise<GenerationAccepted>>()
const listeners = new Set<() => void>()
let sequence = 0

function persistAccepted(entry: TrackedGeneration): void {
  if (!entry.accepted) return
  const current = readJson<Record<string, StoredTrackedGeneration>>(STORAGE_KEY, {})
  const next: Record<string, StoredTrackedGeneration> = {
    ...current,
    [entry.trackingId]: {
      trackingId: entry.trackingId,
      kind: entry.kind,
      projectId: entry.projectId,
      parentGenerationId: entry.parentGenerationId,
      startedAt: entry.startedAt,
      settledAt: entry.settledAt,
      accepted: entry.accepted,
    },
  }
  const entries = Object.entries(next).sort(([, a], [, b]) => a.startedAt - b.startedAt)
  const trimmed = entries.length > MAX_STORED_RUNS ? Object.fromEntries(entries.slice(entries.length - MAX_STORED_RUNS)) : next
  writeJson(STORAGE_KEY, trimmed)
}

function readPersisted(id: string): TrackedGeneration | undefined {
  const stored = readJson<Record<string, StoredTrackedGeneration>>(STORAGE_KEY, {})
  const direct = stored[id]
  const matched = direct ?? Object.values(stored).find((item) => item.accepted?.generation_id === id)
  if (!matched || !matched.accepted?.generation_id) return undefined
  const hydrated: TrackedGeneration = {
    trackingId: matched.trackingId,
    kind: matched.kind,
    projectId: matched.projectId,
    parentGenerationId: matched.parentGenerationId,
    startedAt: matched.startedAt,
    settledAt: matched.settledAt,
    status: 'accepted',
    accepted: matched.accepted,
    error: null,
  }
  runs.set(hydrated.trackingId, hydrated)
  byGenerationId.set(matched.accepted.generation_id, hydrated.trackingId)
  return hydrated
}

function emit(): void {
  for (const listener of listeners) listener()
}

function patch(trackingId: string, changes: Partial<TrackedGeneration>): void {
  const current = runs.get(trackingId)
  if (!current) return
  const updated = { ...current, ...changes }
  runs.set(trackingId, updated)
  if (updated.status === 'accepted' && updated.accepted) {
    persistAccepted(updated)
  }
  emit()
}

function toError(reason: unknown): Error {
  return reason instanceof Error ? reason : new Error(String(reason))
}

function track(kind: TrackedGeneration['kind'], projectId: string | null, parentGenerationId: string | null, submit: () => Promise<GenerationAccepted>): string {
  sequence += 1
  const trackingId = `${TRACKING_PREFIX}${Date.now().toString(36)}-${sequence}`
  runs.set(trackingId, {
    trackingId,
    kind,
    projectId,
    parentGenerationId,
    startedAt: Date.now(),
    settledAt: null,
    status: 'pending',
    accepted: null,
    error: null,
  })
  const submission = new Promise<GenerationAccepted>((resolve) => resolve(submit()))
  submissions.set(trackingId, submission)
  emit()
  submission.then(
    (accepted) => {
      byGenerationId.set(accepted.generation_id, trackingId)
      patch(trackingId, { status: 'accepted', accepted, settledAt: Date.now() })
    },
    (reason: unknown) => patch(trackingId, { status: 'rejected', error: toError(reason), settledAt: Date.now() }),
  )
  return trackingId
}

export function isTrackingId(id: string): boolean {
  return id.startsWith(TRACKING_PREFIX)
}

export function startGeneration(projectId: string, body: GenerationCreate): string {
  const payload: GenerationCreate = { progressive_previews: true, ...body }
  return track('create', projectId, null, () => submitGeneration(projectId, payload))
}

export function retryGeneration(generationId: string): string {
  return track('retry', null, generationId, () => submitRetry(generationId))
}

export function getTrackedGeneration(id: string): TrackedGeneration | undefined {
  const trackingId = runs.has(id) ? id : byGenerationId.get(id)
  if (trackingId) return runs.get(trackingId)
  return readPersisted(id)
}

export function whenGenerationAccepted(trackingId: string): Promise<GenerationAccepted> | undefined {
  return submissions.get(trackingId)
}

export function subscribeToGenerations(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
