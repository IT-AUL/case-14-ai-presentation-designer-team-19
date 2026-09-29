import { useCallback } from 'react'
import { useNavigate } from 'react-router'
import { useRetryGeneration, whenGenerationAccepted } from '@/entities/generation'
import { rememberRun } from '@/entities/project'
import { routes } from '@/shared/config'
import { useToast } from '@/shared/ui'

export interface RerunGeneration {
  rerun: (generationId: string) => void
  isPending: boolean
}

export function useRerunGeneration(projectId: string): RerunGeneration {
  const retry = useRetryGeneration()
  const navigate = useNavigate()
  const toast = useToast()
  const { mutate } = retry

  const rerun = useCallback(
    (generationId: string) => {
      mutate(generationId, {
        onSuccess: (trackingId) => {
          whenGenerationAccepted(trackingId)?.then(
            (accepted) => rememberRun(projectId, accepted.generation_id),
            () => undefined,
          )
          navigate(routes.run(projectId, trackingId))
        },
        onError: (error) => toast.show(error.message),
      })
    },
    [mutate, navigate, projectId, toast],
  )

  return { rerun, isPending: retry.isPending }
}
