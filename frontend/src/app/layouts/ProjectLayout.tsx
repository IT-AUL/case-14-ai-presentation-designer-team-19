import { Outlet, useLocation, useMatch, useParams } from 'react-router'
import { useOpenCounts } from '@/entities/audit'
import { isTrackingId, useGenerationTracker } from '@/entities/generation'
import { projectRunId, useProject, useProjectEvidence } from '@/entities/project'
import { readPlanRequest } from '@/features/edit-deck-plan'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { ProjectRail, stepFromPath, type ProjectProgress } from '@/widgets/project-rail'
import type { ProjectStep } from '@/shared/config'
import { AppFrame } from './AppFrame'
import { runStatus } from './runStatus'

const COMPACT_STEPS: (ProjectStep | undefined)[] = ['variants', 'audit']

export function ProjectLayout() {
  const { projectId = '' } = useParams()
  const { pathname } = useLocation()
  const runMatch = useMatch('/projects/:projectId/runs/:runId/*')
  const { data: project } = useProject(projectId)
  const evidence = useProjectEvidence(projectId)
  const step = stepFromPath(pathname)
  const runId = runMatch?.params.runId ?? projectRunId(project, projectId)
  const tracker = useGenerationTracker(runId)
  const planFirst = useCapabilityFlag(FEATURE_PATHS.planOnly)
  const openCounts = useOpenCounts(projectId, runId)

  const progress: ProjectProgress = {
    projectId,
    runId,
    runPending: Boolean(runId && isTrackingId(runId)),
    hasTemplate: Boolean(project?.template_id),
    hasContent: Boolean(project?.content_pack_id),
    targetSlides: project?.target_slide_count ?? 12,
    planFirst,
    hasPlanDraft: planFirst && readPlanRequest(projectId) !== null,
    generated: Boolean(evidence.generated) || tracker.phase === 'completed',
    repaired: Boolean(evidence.repaired),
    exported: Boolean(evidence.exported),
    reaudited: Boolean(evidence.reaudited),
  }

  return (
    <AppFrame
      projectName={project?.name ?? '…'}
      status={runStatus(step, progress, tracker, openCounts)}
      rail={<ProjectRail progress={progress} current={step} compact={COMPACT_STEPS.includes(step)} />}
    >
      <Outlet />
    </AppFrame>
  )
}
