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

const TRACKING_PREFIX = 'local-'

const runs = new Map<string, TrackedGeneration>()
const byGenerationId = new Map<string, string>()
const submissions = new Map<string, Promise<GenerationAccepted>>()
const listeners = new Set<() => void>()
let sequence = 0

function emit(): void {
  for (const listener of listeners) listener()
}

function patch(trackingId: string, changes: Partial<TrackedGeneration>): void {
  const current = runs.get(trackingId)
  if (!current) return
  runs.set(trackingId, { ...current, ...changes })
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
  return track('create', projectId, null, () => submitGeneration(projectId, body))
}

export function retryGeneration(generationId: string): string {
  return track('retry', null, generationId, () => submitRetry(generationId))
}

export function getTrackedGeneration(id: string): TrackedGeneration | undefined {
  const trackingId = runs.has(id) ? id : byGenerationId.get(id)
  return trackingId ? runs.get(trackingId) : undefined
}

export function whenGenerationAccepted(trackingId: string): Promise<GenerationAccepted> | undefined {
  return submissions.get(trackingId)
}

export function subscribeToGenerations(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}
