import { useQuery } from '@tanstack/react-query'
import { api, isUnsupportedEndpoint, unwrap } from '@/shared/api'
import { pollWithBackoff, type PollableQuery } from '@/shared/lib/polling'
import {
  hasPendingVariantChanges,
  normalizeSlideChangeItem,
  normalizeVariantChangesSnapshot,
  type SlideChangeItem,
  type SlideChangeRequest,
  type VariantChangesSnapshot,
} from '../model/changes'
import { variantKeys } from './variantApi'

const CHANGE_POLL_INTERVAL_MS = 800

async function optional<T>(call: Promise<T>): Promise<T | null> {
  try {
    return await call
  } catch (error) {
    if (isUnsupportedEndpoint(error)) return null
    throw error
  }
}

export async function fetchVariantChanges(variantId: string): Promise<VariantChangesSnapshot | null> {
  const raw = await optional(
    unwrap(
      api.GET('/api/v1/variants/{variant_id}/changes' as never, {
        params: { path: { variant_id: variantId } },
      } as never),
    ) as Promise<Record<string, unknown>>,
  )
  return raw ? normalizeVariantChangesSnapshot(raw, variantId) : null
}

export async function submitVariantChange(
  variantId: string,
  body: SlideChangeRequest,
): Promise<SlideChangeItem | null> {
  const raw = await optional(
    unwrap(
      api.POST('/api/v1/variants/{variant_id}/changes' as never, {
        params: { path: { variant_id: variantId } },
        body,
      } as never),
    ) as Promise<Record<string, unknown>>,
  )
  return raw ? normalizeSlideChangeItem(raw) : null
}

export async function cancelVariantChange(variantId: string, changeId: string): Promise<SlideChangeItem | null> {
  const raw = await optional(
    unwrap(
      api.POST('/api/v1/variants/{variant_id}/changes/{change_id}/cancel' as never, {
        params: { path: { variant_id: variantId, change_id: changeId } },
      } as never),
    ) as Promise<Record<string, unknown>>,
  )
  return raw ? normalizeSlideChangeItem(raw) : null
}

export function regenerateVariantSlide(
  variantId: string,
  slideId: string,
  body: { action: string; chart_type?: string | null; text?: string | null },
): Promise<Record<string, unknown>> {
  return unwrap(
    api.POST('/api/v1/variants/{variant_id}/slides/{slide_id}/regenerate' as never, {
      params: { path: { variant_id: variantId, slide_id: slideId } },
      body,
    } as never),
  ) as Promise<Record<string, unknown>>
}

export function useVariantChanges(variantId: string | null | undefined, options?: { pollAlways?: boolean }) {
  const pollAlways = Boolean(options?.pollAlways)
  return useQuery({
    queryKey: variantKeys.changes(variantId ?? ''),
    queryFn: () => fetchVariantChanges(variantId as string),
    enabled: Boolean(variantId),
    retry: false,
    refetchInterval: (query: PollableQuery<VariantChangesSnapshot | null>) =>
      pollWithBackoff(
        query,
        (data) => Boolean(data && (pollAlways || hasPendingVariantChanges(data))),
        CHANGE_POLL_INTERVAL_MS,
      ),
  })
}
