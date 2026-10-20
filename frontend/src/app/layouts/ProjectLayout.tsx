import { Outlet, useLocation, useMatch, useParams } from 'react-router'
import { useAssistantSession } from '@/entities/assistant'
import { useOpenCounts } from '@/entities/audit'
import { isTrackingId, useGenerationTracker } from '@/entities/generation'
import { projectRunId, useProject, useProjectEvidence } from '@/entities/project'
import { readPlanRequest } from '@/features/edit-deck-plan'
import { AssistantBar, assistantStepFromPath, buildAssistantSteps, useAssistantBarNote } from '@/widgets/assistant-bar'
import { stepFromPath, type ProjectProgress } from '@/widgets/project-rail'
import { AppFrame } from './AppFrame'
import { progressSnapshot } from './assistantSnapshot'
import { runStatus } from './runStatus'

function barStepNote(assistantStep: number | undefined, progress: ProjectProgress, openCounts: { blocker: number; error: number } | null): string | null {
  if (assistantStep !== 7 || !progress.generated) return null
  const open = (openCounts?.blocker ?? 0) + (openCounts?.error ?? 0)
  if (open > 0) return `Вижу ${open} открытых замечаний — могу исправить их за вас или разберёте вручную.`
  return 'Колода собрана — скачайте PPTX, PDF или HTML справа.'
}

export function ProjectLayout() {
  const { projectId = '' } = useParams()
  const { pathname } = useLocation()
  const runMatch = useMatch('/projects/:projectId/runs/:runId/*')
  const { data: project } = useProject(projectId)
  const evidence = useProjectEvidence(projectId)
  const step = stepFromPath(pathname)
  const runId = runMatch?.params.runId ?? projectRunId(project, projectId)
  const tracker = useGenerationTracker(runId)
  const openCounts = useOpenCounts(projectId, runId)
  const reportedNote = useAssistantBarNote(projectId)
  const { data: session } = useAssistantSession(projectId)
  const assistantStep = assistantStepFromPath(pathname)
  const beforeBuild = assistantStep !== undefined && assistantStep <= 5

  const progress: ProjectProgress = {
    projectId,
    runId,
    runPending: Boolean(runId && isTrackingId(runId)),
    hasTemplate: Boolean(project?.template_id),
    hasContent: Boolean(project?.content_pack_id),
    targetSlides: project?.target_slide_count ?? 12,
    planFirst: true,
    hasPlanDraft: readPlanRequest(projectId) !== null,
    generated: Boolean(evidence.generated) || tracker.phase === 'completed',
    polishClear: openCounts ? openCounts.blocker === 0 : undefined,
    repaired: Boolean(evidence.repaired),
    exported: Boolean(evidence.exported),
    reaudited: Boolean(evidence.reaudited),
  }

  return (
    <AppFrame
      projectName={project?.name ?? '…'}
      status={beforeBuild ? undefined : runStatus(step, progress, tracker, openCounts)}
      top={
        <AssistantBar
          steps={buildAssistantSteps(progressSnapshot(progress, session), assistantStep)}
          note={reportedNote ?? barStepNote(assistantStep, progress, openCounts)}
        />
      }
    >
      <Outlet />
    </AppFrame>
  )
}
