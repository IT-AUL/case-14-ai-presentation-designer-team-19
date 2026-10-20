import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, isApiError } from '@/shared/api'
import { pollWithBackoff, type PollableQuery } from '@/shared/lib/polling'
import { isTerminalState, POLL_INTERVAL_MS } from '../model/state'
import { retryGeneration } from '../model/tracker'
import type { GenerationDetail, Job, SlideDraftsResponse } from '../model/types'
import { fetchGeneration, fetchJob, fetchSlideDrafts, requestCancel } from './requests'

export const generationKeys = {
  all: ['generation'] as const,
  detail: (generationId: string) => ['generation', generationId] as const,
  job: (jobId: string) => ['job', jobId] as const,
  slideDrafts: (variantId: string) => ['generation', 'slide-drafts', variantId] as const,
}

function pollUntilTerminal(query: PollableQuery<{ state: GenerationDetail['state'] }>): number | false {
  return pollWithBackoff(query, (data) => !data || !isTerminalState(data.state), POLL_INTERVAL_MS)
}

export function useGeneration(generationId: string | null | undefined) {
  return useQuery({
    queryKey: generationKeys.detail(generationId ?? ''),
    queryFn: () => fetchGeneration(generationId as string),
    enabled: Boolean(generationId),
    refetchInterval: pollUntilTerminal,
  })
}

export function useJob(jobId: string | null | undefined) {
  return useQuery({
    queryKey: generationKeys.job(jobId ?? ''),
    queryFn: () => fetchJob(jobId as string),
    enabled: Boolean(jobId),
    refetchInterval: pollUntilTerminal,
  })
}

export function useSlideDrafts(variantId: string | null | undefined, active = true) {
  const enabled = Boolean(variantId && variantId.startsWith('var_'))
  return useQuery({
    queryKey: generationKeys.slideDrafts(variantId ?? ''),
    queryFn: ({ signal }): Promise<SlideDraftsResponse | null> => fetchSlideDrafts(variantId as string, signal),
    enabled,
    refetchInterval: (query) => {
      if (!active) return false
      if (query.state.data === null) return false
      const state = query.state.data?.state
      if (typeof state === 'string' && isTerminalState(state as GenerationDetail['state'])) return false
      return pollWithBackoff(query, () => true, POLL_INTERVAL_MS)
    },
  })
}

export function useCancelGeneration() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (generationId: string): Promise<Job> => {
      const current = await fetchGeneration(generationId)
      if (isTerminalState(current.state)) {
        queryClient.setQueryData(generationKeys.detail(generationId), current)
        throw new ApiError({ code: 'state_conflict', message: 'Генерация уже завершена, отменять нечего', status: 409 })
      }
      try {
        return await requestCancel(generationId)
      } catch (error) {
        if (isApiError(error) && error.code === 'state_conflict') {
          await queryClient.invalidateQueries({ queryKey: generationKeys.detail(generationId) })
          throw new ApiError({ code: 'state_conflict', message: 'Генерация уже завершена, отменять нечего', status: 409 })
        }
        throw error
      }
    },
    onSuccess: async (job, generationId) => {
      queryClient.setQueryData(generationKeys.job(job.id), job)
      await queryClient.invalidateQueries({ queryKey: generationKeys.detail(generationId) })
    },
  })
}

export function useRetryGeneration() {
  return useMutation({
    mutationFn: async (generationId: string): Promise<string> => retryGeneration(generationId),
  })
}
