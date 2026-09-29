import { DEFAULT_STRATEGY, type VariantSummary } from '@/entities/variant'
import { formatNumber } from '@/shared/lib/format'

export type RecommendationKind = 'quality' | 'default'

export interface Recommendation {
  variantId: string
  kind: RecommendationKind
  reason: string | null
}

export type CriticalCounts = Readonly<Record<string, number | null | undefined>>

type Candidate = Pick<VariantSummary, 'id' | 'strategy' | 'status'>

function fallback(variants: readonly Candidate[]): Recommendation | null {
  const preferred = variants.find((variant) => variant.strategy === DEFAULT_STRATEGY)
  return preferred ? { variantId: preferred.id, kind: 'default', reason: null } : null
}

export function recommendVariant(variants: readonly Candidate[], critical: CriticalCounts): Recommendation | null {
  const ready = variants.filter((variant) => variant.status === 'completed')
  if (ready.length < 2 || ready.length !== variants.length) return fallback(variants)

  const known = ready.flatMap((variant) => {
    const count = critical[variant.id]
    return typeof count === 'number' ? [{ variant, count }] : []
  })
  if (known.length !== ready.length) return fallback(variants)

  const min = Math.min(...known.map(({ count }) => count))
  const best = known.filter(({ count }) => count === min)
  if (best.length === known.length) return fallback(variants)

  const chosen = best.find(({ variant }) => variant.strategy === DEFAULT_STRATEGY) ?? best[0]
  if (!chosen) return fallback(variants)

  const next = Math.min(...known.filter(({ count }) => count !== min).map(({ count }) => count))
  const reason =
    best.length === 1
      ? `Меньше всего критичных проблем — ${formatNumber(min)}, у других от ${formatNumber(next)}.`
      : `Меньше всего критичных проблем — ${formatNumber(min)}.`
  return { variantId: chosen.variant.id, kind: 'quality', reason }
}
