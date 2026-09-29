import type { GenerationDetail, JobState } from '@/entities/generation'
import { JOB_STATE_LABEL } from '@/entities/generation'
import type { Project } from '@/entities/project'
import { routes } from '@/shared/config'
import { pluralize } from '@/shared/lib/format'
import type { Tone } from '@/shared/ui'

export type RunLookup = { kind: 'none' } | { kind: 'loading' } | { kind: 'missing' } | { kind: 'ready'; generation: GenerationDetail }

export interface ProjectStatusView {
  label: string
  tone: Tone
  stage: string
}

const STATE_TONE: Record<JobState, Tone> = {
  queued: 'neutral',
  running: 'info',
  awaiting_user: 'warn',
  completed: 'ok',
  failed: 'error',
  canceled: 'muted',
}

function runStage(generation: GenerationDetail): string {
  switch (generation.state) {
    case 'completed': {
      const count = generation.variants.length
      return count ? `Готово · ${count} ${pluralize(count, ['вариант', 'варианта', 'вариантов'])}` : 'Готово'
    }
    case 'running':
      return 'Идёт генерация вариантов'
    case 'queued':
      return 'В очереди на генерацию'
    case 'awaiting_user':
      return 'Ждёт решения пользователя'
    case 'failed':
      return 'Ошибка генерации'
    case 'canceled':
      return 'Генерация отменена'
  }
}

function setupStage(project: Project): ProjectStatusView {
  if (!project.template_id) return { label: 'Черновик', tone: 'neutral', stage: 'Нужен шаблон' }
  if (!project.content_pack_id) return { label: 'Черновик', tone: 'neutral', stage: 'Шаблон разобран · нужен бриф' }
  return { label: 'Готов к генерации', tone: 'info', stage: 'Бриф и контент загружены' }
}

export function projectStatus(project: Project, run: RunLookup): ProjectStatusView | null {
  if (run.kind === 'loading') return null
  if (run.kind === 'ready') {
    const { generation } = run
    return { label: JOB_STATE_LABEL[generation.state], tone: STATE_TONE[generation.state], stage: runStage(generation) }
  }
  const setup = setupStage(project)
  if (project.status === 'archived') return { ...setup, label: 'В архиве', tone: 'muted' }
  return setup
}

export function projectHref(project: Pick<Project, 'id'>, runId: string | undefined, run: RunLookup): string {
  if (runId && run.kind !== 'missing') return routes.variants(project.id, runId)
  return routes.template(project.id)
}

export function sortProjects(projects: readonly Project[]): Project[] {
  return [...projects].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at))
}
