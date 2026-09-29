import { stageLabel, type GenerationPhase } from '@/entities/generation'
import { isVariantSettled, orderVariants, STRATEGY_ORDER, strategyInfo, type StrategyInfo, type VariantSummary } from '@/entities/variant'

export type VariantCardState = 'pending' | 'queued' | 'running' | 'done' | 'error' | 'canceled'

export interface VariantCard {
  key: string
  strategy: StrategyInfo
  state: VariantCardState
  variant: VariantSummary | null
}

function stateOf(variant: VariantSummary): VariantCardState {
  switch (variant.status) {
    case 'queued':
      return 'queued'
    case 'running':
    case 'awaiting_user':
      return 'running'
    case 'failed':
      return 'error'
    case 'canceled':
      return 'canceled'
    case 'completed':
      return isVariantSettled(variant) ? 'done' : 'running'
  }
}

export function variantCards(phase: GenerationPhase, variants: readonly VariantSummary[] | undefined): VariantCard[] {
  if (variants && variants.length > 0) {
    return orderVariants(variants).map((variant) => ({
      key: variant.id,
      strategy: strategyInfo(variant.strategy),
      state: stateOf(variant),
      variant,
    }))
  }
  if (phase !== 'submitting' && phase !== 'running') return []
  return STRATEGY_ORDER.map((strategy) => ({ key: strategy, strategy: strategyInfo(strategy), state: 'pending', variant: null }))
}

function parseTime(value: string | null | undefined): number | null {
  if (!value) return null
  const time = Date.parse(value)
  return Number.isNaN(time) ? null : time
}

export function variantElapsedSeconds(variant: Pick<VariantSummary, 'started_at' | 'finished_at'> | null, nowMs: number): number | null {
  const started = parseTime(variant?.started_at)
  if (started === null) return null
  const finished = parseTime(variant?.finished_at) ?? nowMs
  return Math.max(0, (finished - started) / 1000)
}

export function variantStageLabel(variant: Pick<VariantSummary, 'stage'> | null): string | null {
  return stageLabel(variant?.stage)
}
