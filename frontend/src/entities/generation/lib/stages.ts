import { JOB_STATE_LABEL } from '../model/state'
import type { Job } from '../model/types'

export type PipelineStage = 'template' | 'content' | 'plan' | 'layout' | 'audit' | 'repair' | 'render' | 'passport'

export const PIPELINE_STAGE_LABEL: Record<PipelineStage, string> = {
  template: 'Анализ шаблона',
  content: 'Разбор контента',
  plan: 'План структуры',
  layout: 'Вёрстка',
  audit: 'Аудит',
  repair: 'Исправление',
  render: 'Рендер',
  passport: 'Паспорт качества',
}

export const GENERATION_PIPELINE: readonly PipelineStage[] = ['content', 'plan', 'layout', 'audit', 'render', 'passport']

const STAGE_PATTERNS: readonly (readonly [PipelineStage, RegExp])[] = [
  ['passport', /passport|quality/],
  ['repair', /repair|fix/],
  ['audit', /audit|check|lint|vlm|critic/],
  ['render', /render|pdf|preview|montage|export|html/],
  ['plan', /plan|story|outline|structure|narrative/],
  ['layout', /layout|compos|compil|assembl|build|slot|fill|pptx|variant/],
  ['content', /content|ingest|pars|evidence|extract/],
  ['template', /template|analy[sz]|dna/],
]

export function pipelineStage(stage: string | null | undefined): PipelineStage | null {
  if (!stage) return null
  const normalized = stage.trim().toLowerCase()
  return STAGE_PATTERNS.find(([, pattern]) => pattern.test(normalized))?.[0] ?? null
}

function humanize(stage: string): string {
  const text = stage.trim().replace(/[_.:/-]+/g, ' ').replace(/\s+/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export function stageLabel(stage: string | null | undefined): string | null {
  if (!stage?.trim()) return null
  const known = pipelineStage(stage)
  return known ? PIPELINE_STAGE_LABEL[known] : humanize(stage)
}

export function jobStatusLabel(job: Pick<Job, 'state' | 'stage' | 'error'>): string {
  if (job.state === 'failed') {
    const failedAt = stageLabel(job.error?.stage ?? job.stage)
    return failedAt ? `Ошибка на этапе «${failedAt.toLowerCase()}»` : JOB_STATE_LABEL.failed
  }
  if (job.state === 'running') {
    const current = stageLabel(job.stage)
    return current ? `Идёт: ${current.toLowerCase()}` : JOB_STATE_LABEL.running
  }
  return JOB_STATE_LABEL[job.state]
}
