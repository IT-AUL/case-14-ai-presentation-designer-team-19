import { useCallback, useSyncExternalStore } from 'react'
import { ApiError } from '@/shared/api'
import { isTransientError } from '@/shared/lib/polling'
import { useGeneration, useJob } from '../api/generationApi'
import { resolvePhase, resolveTiming, type GenerationPhase } from './phase'
import { isTerminalState } from './state'
import { getTrackedGeneration, isTrackingId, subscribeToGenerations, type TrackedGeneration } from './tracker'
import type { GenerationDetail, Job, JobError } from './types'

export interface GenerationTracker {
  trackingId: string | null
  generationId: string | null
  jobId: string | null
  variantIds: string[]
  phase: GenerationPhase
  startedAt: number | null
  finishedAt: number | null
  generation: GenerationDetail | undefined
  job: Job | undefined
  error: Error | null
  jobError: JobError | null
  canCancel: boolean
  reconnecting?: boolean
  refetch?: () => void
}

const TRACKING_LOST = new ApiError({
  code: 'tracking_lost',
  message: 'Страница была перезагружена до ответа сервиса, результат этого запуска недоступен',
  status: 0,
})

export function useGenerationTracker(id: string | null | undefined): GenerationTracker {
  const tracked: TrackedGeneration | undefined = useSyncExternalStore(subscribeToGenerations, () => (id ? getTrackedGeneration(id) : undefined))
  const trackingLost = Boolean(id && isTrackingId(id) && !tracked)
  const generationId = tracked ? (tracked.accepted?.generation_id ?? null) : trackingLost ? null : (id ?? null)

  const generationQuery = useGeneration(generationId)
  const generation = generationQuery.data
  const jobId = tracked?.accepted?.job_id ?? generation?.job_id ?? null
  const jobQuery = useJob(jobId)
  const job = jobQuery.data
  const reconnecting = generationQuery.isError && isTransientError(generationQuery.error) && !(generation && isTerminalState(generation.state))
  const { refetch: refetchGeneration } = generationQuery
  const { refetch: refetchJob } = jobQuery
  const refetch = useCallback(() => {
    void refetchGeneration()
    if (jobId) void refetchJob()
  }, [refetchGeneration, refetchJob, jobId])

  const phase = resolvePhase({ tracked, trackingLost, generation, generationError: generationQuery.error, reconnecting })
  const { startedAt, finishedAt } = resolveTiming(tracked, generation)

  return {
    trackingId: tracked?.trackingId ?? null,
    generationId,
    jobId,
    variantIds: tracked?.accepted?.variant_ids ?? generation?.variants.map((variant) => variant.id) ?? [],
    phase,
    startedAt,
    finishedAt,
    generation,
    job,
    error: trackingLost ? TRACKING_LOST : (tracked?.error ?? generationQuery.error ?? null),
    jobError: job?.error ?? null,
    canCancel: phase === 'running' && generationId !== null,
    reconnecting,
    refetch,
  }
}
