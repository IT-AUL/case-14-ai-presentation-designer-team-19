import { useState } from 'react'
import { useNavigate } from 'react-router'
import { startGeneration, whenGenerationAccepted, type DeckPlan, type GenerationCreate } from '@/entities/generation'
import { rememberRun } from '@/entities/project'
import { routes } from '@/shared/config'
import { prepareForBuild, type BuildOptions } from '../lib/editPlan'
import { clearPlanDraft } from './usePlanDraft'

export interface BuildFromPlan {
  build: (body: Omit<GenerationCreate, 'deck_plan' | 'deck_plan_id'>, plan: DeckPlan, options?: BuildOptions) => void
  isPending: boolean
}

export function useBuildFromPlan(projectId: string, draftKey: string): BuildFromPlan {
  const navigate = useNavigate()
  const [isPending, setPending] = useState(false)

  const build: BuildFromPlan['build'] = (body, plan, options) => {
    setPending(true)
    const trackingId = startGeneration(projectId, { ...body, deck_plan: prepareForBuild(plan, options) })
    whenGenerationAccepted(trackingId)?.then(
      (accepted) => {
        rememberRun(projectId, accepted.generation_id)
        clearPlanDraft(draftKey)
      },
      () => undefined,
    )
    navigate(routes.run(projectId, trackingId))
  }

  return { build, isPending }
}
