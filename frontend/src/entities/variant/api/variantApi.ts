import { useQuery } from '@tanstack/react-query'
import { api, unwrap } from '@/shared/api'
import { pollWithBackoff, type PollableQuery } from '@/shared/lib/polling'
import { isVariantSettled } from '../model/status'
import type { SlideInfo, VariantSummary } from '../model/types'

const POLL_INTERVAL_MS = 2000
const SLIDE_PAGE_LIMIT = 200

export const variantKeys = {
  all: ['variant'] as const,
  forGeneration: (generationId: string) => ['variant', 'generation', generationId] as const,
  detail: (variantId: string) => ['variant', variantId] as const,
  slides: (variantId: string) => ['variant', variantId, 'slides'] as const,
  changes: (variantId: string) => ['variant', variantId, 'changes'] as const,
}

function pollWhile<T>(unsettled: (data: T) => boolean) {
  return (query: PollableQuery<T>): number | false => pollWithBackoff(query, (data) => data !== undefined && unsettled(data), POLL_INTERVAL_MS)
}

export function useVariants(generationId: string | null | undefined) {
  return useQuery({
    queryKey: variantKeys.forGeneration(generationId ?? ''),
    queryFn: async (): Promise<VariantSummary[]> =>
      (await unwrap(api.GET('/api/v1/generations/{run_id}/variants', { params: { path: { run_id: generationId as string } } }))).items,
    enabled: Boolean(generationId),
    refetchInterval: pollWhile<VariantSummary[]>((variants) => variants.some((variant) => !isVariantSettled(variant))),
  })
}

export function useVariant(variantId: string | null | undefined) {
  return useQuery({
    queryKey: variantKeys.detail(variantId ?? ''),
    queryFn: (): Promise<VariantSummary> => unwrap(api.GET('/api/v1/variants/{variant_id}', { params: { path: { variant_id: variantId as string } } })),
    enabled: Boolean(variantId),
    refetchInterval: pollWhile<VariantSummary>((variant) => !isVariantSettled(variant)),
  })
}

export function useVariantSlides(variantId: string | null | undefined, isRunning = false) {
  return useQuery({
    queryKey: variantKeys.slides(variantId ?? ''),
    queryFn: async (): Promise<SlideInfo[]> => {
      const items: SlideInfo[] = []
      const cursors = new Set<string>()
      let cursor: string | undefined
      do {
        const page = await unwrap(api.GET('/api/v1/variants/{variant_id}/slides', {
          params: { path: { variant_id: variantId as string }, query: { limit: SLIDE_PAGE_LIMIT, ...(cursor ? { cursor } : {}) } },
        }))
        items.push(...page.items)
        cursor = page.next_cursor ?? undefined
        if (cursor && cursors.has(cursor)) throw new Error('Не удалось прочитать все слайды')
        if (cursor) cursors.add(cursor)
      } while (cursor)
      return [...new Map(items.map((item) => [item.id, item])).values()].sort((a, b) => a.index - b.index)
    },
    enabled: Boolean(variantId),
    refetchInterval: isRunning ? 2500 : false,
  })
}
