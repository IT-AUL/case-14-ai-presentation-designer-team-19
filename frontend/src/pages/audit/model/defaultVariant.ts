import { DEFAULT_STRATEGY, orderVariants, type VariantSummary } from '@/entities/variant'

export function defaultAuditVariant(variants: readonly VariantSummary[]): VariantSummary | null {
  const ordered = orderVariants(variants)
  const completed = ordered.filter((variant) => variant.status === 'completed')
  const pool = completed.length > 0 ? completed : ordered
  return pool.find((variant) => variant.strategy === DEFAULT_STRATEGY) ?? pool[0] ?? null
}

export function parseSlideParam(value: string | null): number | null {
  if (!value) return null
  const number = Number(value)
  return Number.isInteger(number) && number >= 1 ? number : null
}
