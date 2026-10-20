import { useSyncExternalStore } from 'react'
import { useMutation } from '@tanstack/react-query'
import { parsePlanDraft, requestPlans, type ContentFillMode, type DeckPlan, type PlanDraftResult, type PlanRequest, type PlanRequestOptions, type VariantRequest } from '@/entities/generation'
import { api, isUnsupportedEndpoint, requestOptionalJson, unwrap } from '@/shared/api'
import { readJson, removeKey, writeJson } from '@/shared/lib/storage'

const STORAGE_PREFIX = 'deckdna.plan-request.v1:'

export type StoredRequest = PlanRequest & { template_id: string }

export interface StoredPlanRequest {
  request: StoredRequest
  result: PlanDraftResult
  variants?: VariantRequest[]
  contentFill?: ContentFillMode | null
  keyMessage?: string | null
  assistantNote?: string | null
  createdAt: string
}

export interface PlanRequestExtras {
  variants?: VariantRequest[]
  contentFill?: ContentFillMode | null
  keyMessage?: string | null
  assistantNote?: string | null
}

function storageKey(projectId: string): string {
  return `${STORAGE_PREFIX}${projectId}`
}

export function readPlanRequest(projectId: string): StoredPlanRequest | null {
  const stored = readJson<StoredPlanRequest | null>(storageKey(projectId), null)
  if (!stored || !Array.isArray(stored.result?.proposals) || stored.result.proposals.length === 0) return null
  return {
    ...stored,
    result: { ...stored.result, sufficiency: stored.result.sufficiency ?? null, research: stored.result.research ?? null },
  }
}

export function writePlanRequest(projectId: string, stored: StoredPlanRequest): StoredPlanRequest {
  writeJson(storageKey(projectId), stored)
  return stored
}

export function clearPlanRequest(projectId: string): void {
  if (!projectId) return
  removeKey(storageKey(projectId))
}

export function syncPlanDraftToStorage(projectId: string, stored: StoredPlanRequest, deckPlan: DeckPlan, strategy: string | null): StoredPlanRequest {
  const proposals = stored.result.proposals.map((item, index) => {
    if ((strategy && item.strategy === strategy) || (!strategy && index === 0)) {
      return { ...item, deckPlan }
    }
    return item
  })
  const keyMessage = deckPlan.brief.key_message ?? stored.keyMessage ?? null
  const next: StoredPlanRequest = {
    ...stored,
    keyMessage,
    request: {
      ...stored.request,
      brief: {
        ...stored.request.brief,
        target_slide_count: deckPlan.slides.length,
        ...(keyMessage ? { key_message: keyMessage } : {}),
      },
    },
    result: { ...stored.result, proposals },
  }
  return writePlanRequest(projectId, next)
}

export function applyServerPlanSet(projectId: string, stored: StoredPlanRequest, payload: unknown): StoredPlanRequest {
  const parsed = parsePlanDraft(payload)
  if (parsed.proposals.length === 0) return stored
  const record = typeof payload === 'object' && payload !== null ? (payload as Record<string, unknown>) : {}
  const packId = typeof record.content_pack_id === 'string' && record.content_pack_id ? record.content_pack_id : stored.request.content_pack_id
  const keyMessage = typeof record.key_message === 'string' ? record.key_message : (parsed.proposals[0]?.deckPlan.brief.key_message ?? stored.keyMessage ?? null)
  const assistantNote = typeof record.assistant_note === 'string' ? record.assistant_note : stored.assistantNote ?? null
  const next: StoredPlanRequest = {
    ...stored,
    keyMessage,
    assistantNote,
    request: { ...stored.request, content_pack_id: packId },
    result: {
      planId: parsed.planId ?? stored.result.planId,
      proposals: parsed.proposals,
      sufficiency: parsed.sufficiency ?? stored.result.sufficiency,
      research: parsed.research ?? stored.result.research,
    },
  }
  return writePlanRequest(projectId, next)
}

export async function fetchAndStorePlan(
  projectId: string,
  planId: string,
  fallbackRequest: StoredRequest,
  extras: PlanRequestExtras = {},
  options?: { signal?: AbortSignal },
): Promise<StoredPlanRequest | null> {
  if (!projectId || !planId) return null
  const payload =
    (await requestOptionalJson<unknown>(`/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(planId)}`, { signal: options?.signal })) ??
    (await requestOptionalJson<unknown>(`/plans/${encodeURIComponent(planId)}`, { signal: options?.signal }))
  if (!payload) return null
  const parsed = parsePlanDraft(payload)
  if (parsed.proposals.length === 0) return null
  const record = typeof payload === 'object' && payload !== null ? (payload as Record<string, unknown>) : {}
  const packId = typeof record.content_pack_id === 'string' && record.content_pack_id ? record.content_pack_id : fallbackRequest.content_pack_id
  const templateId = typeof record.template_id === 'string' && record.template_id ? record.template_id : fallbackRequest.template_id
  const deckBrief = parsed.proposals[0]?.deckPlan.brief
  const keyMessage = typeof record.key_message === 'string' ? record.key_message : (extras.keyMessage ?? deckBrief?.key_message ?? fallbackRequest.brief.key_message ?? null)
  const assistantNote = typeof record.assistant_note === 'string' ? record.assistant_note : (extras.assistantNote ?? null)
  const request: StoredRequest = {
    ...fallbackRequest,
    template_id: templateId,
    content_pack_id: packId,
    brief: deckBrief ? { ...fallbackRequest.brief, ...deckBrief } : fallbackRequest.brief,
  }
  const result: PlanDraftResult = { ...parsed, planId: parsed.planId ?? planId }
  const stored: StoredPlanRequest = { request, result, ...extras, keyMessage, assistantNote, createdAt: new Date().toISOString() }
  return writePlanRequest(projectId, stored)
}

export async function requestAndStorePlan(projectId: string, request: StoredRequest, extras: PlanRequestExtras = {}, options?: PlanRequestOptions): Promise<StoredPlanRequest> {
  const result = await requestPlans(projectId, request, options)
  const keyMessage = extras.keyMessage ?? result.proposals[0]?.deckPlan.brief.key_message ?? request.brief.key_message ?? null
  const stored: StoredPlanRequest = { request, result, ...extras, keyMessage, createdAt: new Date().toISOString() }
  return writePlanRequest(projectId, stored)
}

export function useRequestPlan(projectId: string) {
  return useMutation({
    mutationFn: ({ request, extras, options }: { request: StoredRequest; extras?: PlanRequestExtras; options?: PlanRequestOptions }) =>
      requestAndStorePlan(projectId, request, extras, options),
  })
}

export interface SlideResearchOutcome {
  added: string[]
  message: string
}

const slideResearchStore = new Map<string, SlideResearchOutcome>()
const slideResearchListeners = new Set<() => void>()

function emitSlideResearchChange() {
  for (const listener of slideResearchListeners) listener()
}

function subscribeSlideResearch(listener: () => void) {
  slideResearchListeners.add(listener)
  return () => {
    slideResearchListeners.delete(listener)
  }
}

export function setSlideResearchOutcome(slideId: string, outcome: SlideResearchOutcome | null): void {
  if (!slideId) return
  if (outcome === null) {
    slideResearchStore.delete(slideId)
  } else {
    slideResearchStore.set(slideId, outcome)
  }
  emitSlideResearchChange()
}

export function clearSlideResearchOutcomes(): void {
  if (slideResearchStore.size === 0) return
  slideResearchStore.clear()
  emitSlideResearchChange()
}

export function getSlideResearchOutcome(slideId: string): SlideResearchOutcome | null {
  return slideResearchStore.get(slideId) ?? null
}

export function useSlideResearchOutcome(slideId: string): SlideResearchOutcome | null {
  return useSyncExternalStore(
    subscribeSlideResearch,
    () => slideResearchStore.get(slideId) ?? null,
    () => null,
  )
}

export function formatSlideResearchOutcome(result: unknown): SlideResearchOutcome {
  if (!result || typeof result !== 'object') {
    return { added: [], message: 'Новых цифр в материалах не нашлось' }
  }
  const record = result as Record<string, unknown>
  if (Array.isArray(record.added_metrics)) {
    const metrics = record.added_metrics
      .filter((item): item is string => typeof item === 'string' && Boolean(item.trim()))
      .map((item) => item.trim())
    if (metrics.length > 0) {
      return { added: metrics, message: `Нашёл и добавил: ${metrics.join(', ')}` }
    }
    return { added: [], message: 'Новых цифр в материалах не нашлось' }
  }
  const addedCount = typeof record.added_units === 'number' && Number.isFinite(record.added_units) ? record.added_units : 0
  if (addedCount <= 0) {
    return { added: [], message: 'Новых цифр в материалах не нашлось' }
  }
  const slide = typeof record.slide === 'object' && record.slide !== null ? (record.slide as Record<string, unknown>) : null
  const units = Array.isArray(slide?.content_units) ? slide.content_units : []
  const tail = units.slice(-addedCount)
  const items: string[] = []
  for (const rawUnit of tail) {
    if (!rawUnit || typeof rawUnit !== 'object') continue
    const unit = rawUnit as Record<string, unknown>
    if (typeof unit.text === 'string' && unit.text.trim()) {
      items.push(unit.text.trim())
    } else if (unit.kind === 'chart') {
      items.push('график')
    } else if (unit.kind === 'table') {
      items.push('таблица')
    }
  }
  if (items.length > 0) {
    return { added: items, message: `Нашёл и добавил: ${items.join(', ')}` }
  }
  return { added: [`${addedCount}`], message: `Нашёл и добавил: ${addedCount}` }
}

export function useResearchPlanSlide(planId: string) {
  return useMutation({
    mutationFn: async ({ slideId, strategy, focus = 'numbers', useLlm, providerSessionId }: { slideId: string; strategy?: string | null; focus?: 'facts' | 'numbers'; useLlm?: boolean | null; providerSessionId?: string | null }) => {
      if (!planId) return null
      try {
        const result = await unwrap(
          api.POST('/api/v1/plans/{plan_id}/slides/{slide_id}/research', {
            params: { path: { plan_id: planId, slide_id: slideId } },
            body: {
              focus,
              ...(strategy ? { strategy } : {}),
              ...(useLlm !== undefined ? { use_llm: useLlm } : {}),
              ...(providerSessionId ? { provider_session_id: providerSessionId } : {}),
            },
          }),
        )
        setSlideResearchOutcome(slideId, formatSlideResearchOutcome(result))
        return result
      } catch (error) {
        if (isUnsupportedEndpoint(error)) {
          setSlideResearchOutcome(slideId, formatSlideResearchOutcome(null))
          return null
        }
        throw error
      }
    },
  })
}

