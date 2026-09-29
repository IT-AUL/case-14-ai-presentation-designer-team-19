import type { GenerationTiming } from '../lib/budget'
import { isTerminalState } from './state'
import type { TrackedGeneration } from './tracker'
import type { GenerationDetail, JobState } from './types'

export type GenerationPhase = 'submitting' | 'running' | 'completed' | 'failed' | 'canceled'

export interface PhaseInput {
  tracked: TrackedGeneration | undefined
  trackingLost: boolean
  generation: GenerationDetail | undefined
  generationError: Error | null
  reconnecting?: boolean
}

const PHASE_BY_STATE: Record<JobState, GenerationPhase> = {
  queued: 'running',
  running: 'running',
  awaiting_user: 'running',
  completed: 'completed',
  failed: 'failed',
  canceled: 'canceled',
}

export function resolvePhase({ tracked, trackingLost, generation, generationError, reconnecting = false }: PhaseInput): GenerationPhase {
  if (trackingLost) return 'failed'
  if (tracked?.status === 'pending') return 'submitting'
  if (tracked?.status === 'rejected') return 'failed'
  if (generation) return PHASE_BY_STATE[generation.state]
  if (generationError && !reconnecting) return 'failed'
  return 'running'
}

function parseTime(value: string | null | undefined): number | null {
  if (!value) return null
  const time = Date.parse(value)
  return Number.isFinite(time) ? time : null
}

export function resolveTiming(tracked: TrackedGeneration | undefined, generation: GenerationDetail | undefined): GenerationTiming {
  const createdAt = parseTime(generation?.created_at)
  const startedAt = tracked?.startedAt ?? createdAt
  if (tracked?.status === 'rejected') return { startedAt, finishedAt: tracked.settledAt }
  if (!generation || !isTerminalState(generation.state) || startedAt === null) return { startedAt, finishedAt: null }
  const serverFinishedAt = parseTime(generation.finished_at)
  if (createdAt !== null && serverFinishedAt !== null) return { startedAt, finishedAt: startedAt + Math.max(0, serverFinishedAt - createdAt) }
  return { startedAt, finishedAt: tracked?.settledAt ?? serverFinishedAt }
}
