import { GENERATION_PIPELINE, pipelineStage, type PipelineStage } from '@/entities/generation'
import { DEFAULT_STRATEGY, type VariantSummary } from '@/entities/variant'
import type { VariantCard, VariantCardState } from './variants'

const STAGE_PHRASE: Record<PipelineStage, string> = {
  template: 'Сверяемся с правилами шаблона',
  content: 'Разбираем контент',
  plan: 'Выстраиваем историю колоды',
  layout: 'Раскладываем слайды по макетам шаблона',
  audit: 'Проверяем колоду по правилам шаблона',
  repair: 'Исправляем найденное',
  render: 'Рисуем слайды',
  passport: 'Собираем паспорт качества',
}

const STEP_LABEL: Record<PipelineStage, string> = {
  template: 'шаблон',
  content: 'контент',
  plan: 'план',
  layout: 'вёрстка',
  audit: 'аудит',
  repair: 'правки',
  render: 'рендер',
  passport: 'паспорт',
}

const WAITING_PHRASE: Partial<Record<VariantCardState, string>> = {
  pending: 'Готовим сборку',
  queued: 'Ждёт своей очереди',
  canceled: 'Сборка отменена',
  error: 'Вёрстка не завершилась',
}

export type SceneStepState = 'done' | 'active' | 'pending'

export interface SceneStep {
  stage: PipelineStage
  label: string
  state: SceneStepState
}

export interface SceneProgress {
  phrase: string
  steps: SceneStep[]
}

function currentIndex(state: VariantCardState, stage: string | null | undefined): number {
  if (state === 'done') return GENERATION_PIPELINE.length
  if (state !== 'running') return -1
  const known = pipelineStage(stage)
  return known ? GENERATION_PIPELINE.indexOf(known) : 0
}

export function sceneProgress(card: Pick<VariantCard, 'state' | 'variant'>): SceneProgress {
  const stage = card.variant?.stage ?? null
  const index = currentIndex(card.state, stage)
  const known = pipelineStage(stage)
  const phrase = card.state === 'running' ? (known ? STAGE_PHRASE[known] : 'Собираем колоду') : (WAITING_PHRASE[card.state] ?? 'Колода готова')
  return {
    phrase,
    steps: GENERATION_PIPELINE.map((item, position) => ({
      stage: item,
      label: STEP_LABEL[item],
      state: position < index ? 'done' : position === index ? 'active' : 'pending',
    })),
  }
}

export function autoSelectedKey(cards: readonly VariantCard[]): string | null {
  const running = cards.find((card) => card.state === 'running')
  if (running) return running.key
  const pending = cards.find((card) => card.state === 'pending' || card.state === 'queued')
  const done = cards.filter((card) => card.state === 'done')
  if (done.length === cards.length) return (done.find((card) => card.strategy.id === DEFAULT_STRATEGY) ?? done[0])?.key ?? null
  return pending?.key ?? cards[0]?.key ?? null
}

export interface ResultSummary {
  styleFidelity: number | null
  autoFixed: number | null
  openIssues: number | null
  opensCleanly: boolean | null
}

function finite(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function resultSummary(variant: Pick<VariantSummary, 'metrics'>): ResultSummary {
  const metrics = variant.metrics
  const validity = finite(metrics?.validity)
  return {
    styleFidelity: finite(metrics?.style_fidelity),
    autoFixed: finite(metrics?.auto_fixed),
    openIssues: finite(metrics?.issues_total),
    opensCleanly: validity === null ? null : validity >= 1,
  }
}
