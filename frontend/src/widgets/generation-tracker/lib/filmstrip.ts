import type { JobEvent } from '@/entities/assistant'
import {
  pipelineStage,
  slidePurposeLabel,
  type DeckPlan,
  type GenerationPhase,
  type SlideDraftItem,
  type SlidePlan,
} from '@/entities/generation'
import type { SlideInfo } from '@/entities/variant'
import { artifactUrl } from '@/shared/api'
import { formatIndex } from '@/shared/lib/format'
import type { VariantCardState } from './variants'

export type FilmstripSlotState = 'ready' | 'building' | 'draft'
export type SlidePreviewBadge = 'черновик' | 'обновляется' | null

export interface FilmstripSlot {
  key: string
  slideKey: string
  index: number
  slideNumber: number
  slideId: string
  title: string
  keyMessage: string | null
  bullets: string[]
  purposeLabel: string | null
  layoutName: string | null
  previewArtifactId: string | null
  previewRevision: number | null
  previewStatus: string | null
  previewBadge: SlidePreviewBadge
  imageUrl: string | null
  state: FilmstripSlotState
}

export interface BuildFilmstripSlotsOptions {
  plan?: DeckPlan | null
  variantSlides?: readonly SlideInfo[] | null
  slideDrafts?: readonly SlideDraftItem[] | null
  events?: readonly JobEvent[]
  variantId?: string | null
  variantState?: VariantCardState
}

export type BuildStageItemState = 'done' | 'active' | 'pending'

export interface BuildStageItem {
  id: 'plan' | 'slides' | 'audit'
  label: string
  statusLabel: string
  state: BuildStageItemState
}

export interface BuildStageSummary {
  stages: readonly [BuildStageItem, BuildStageItem, BuildStageItem]
  readySlides: number
  totalSlides: number
  draftSlides: number
  updatingSlides: number
}

interface SlidePreviewRecord {
  slideKey: string | null
  slideId: string | null
  index: number | null
  revision: number
  previewRevision: number | null
  previewArtifactId: string | null
  status: string | null
}

const SLIDE_EVENT_TYPES = new Set(['slide.ready', 'slide.updated', 'slide.compiled', 'slide.preview_failed'])

function readString(event: JobEvent, key: string): string | null {
  const inData = event.data[key]
  if (typeof inData === 'string' && inData.trim()) return inData.trim()
  const top = event[key]
  if (typeof top === 'string' && top.trim()) return top.trim()
  return null
}

function readNumber(event: JobEvent, key: string): number | null {
  const inData = event.data[key]
  if (typeof inData === 'number' && Number.isFinite(inData)) return inData
  const top = event[key]
  if (typeof top === 'number' && Number.isFinite(top)) return top
  return null
}

function matchesVariantId(recordVariantId: string | null | undefined, variantId: string | null | undefined): boolean {
  if (!variantId || !variantId.startsWith('var_')) return true
  if (!recordVariantId) return true
  return recordVariantId === variantId
}

function matchesVariant(event: JobEvent, variantId: string | null | undefined): boolean {
  const eventVariantId = readString(event, 'variant_id')
  if (!matchesVariantId(eventVariantId, variantId)) return false
  const slideKey = readString(event, 'slide_key')
  if (slideKey && variantId && variantId.startsWith('var_') && slideKey.includes(':')) {
    const prefix = slideKey.slice(0, slideKey.indexOf(':'))
    if (prefix && prefix !== variantId) return false
  }
  return true
}

function parseSlideKeyIndex(slideKey: string | null | undefined): number | null {
  if (!slideKey) return null
  const colonIdx = slideKey.lastIndexOf(':')
  const raw = colonIdx >= 0 ? slideKey.slice(colonIdx + 1) : slideKey
  const parsed = Number.parseInt(raw, 10)
  return Number.isFinite(parsed) ? parsed : null
}

function progressionRank(record: Pick<SlidePreviewRecord, 'previewRevision' | 'status' | 'previewArtifactId'>): number {
  const status = record.status
  const rev = record.previewRevision
  if ( (rev !== null && rev >= 2) || (status === 'rendered' && rev !== 1 && Boolean(record.previewArtifactId)) ) {
    return 3
  }
  if (status === 'superseded') {
    return 2
  }
  if (rev === 1 || (status === 'draft' && Boolean(record.previewArtifactId))) {
    return 1
  }
  return 0
}

function mergePreviewRecord(existing: SlidePreviewRecord | undefined, incoming: SlidePreviewRecord): SlidePreviewRecord {
  if (!existing) return incoming
  if (incoming.revision < existing.revision) return existing
  if (incoming.revision > existing.revision) {
    return {
      ...incoming,
      previewArtifactId: incoming.previewArtifactId ?? existing.previewArtifactId,
    }
  }
  const existingRank = progressionRank(existing)
  const incomingRank = progressionRank(incoming)
  if (incomingRank < existingRank) {
    return {
      ...existing,
      previewArtifactId: existing.previewArtifactId ?? incoming.previewArtifactId,
    }
  }
  return {
    slideKey: incoming.slideKey ?? existing.slideKey,
    slideId: incoming.slideId ?? existing.slideId,
    index: incoming.index ?? existing.index,
    revision: incoming.revision,
    previewRevision: incoming.previewRevision ?? existing.previewRevision,
    previewArtifactId: incoming.previewArtifactId ?? existing.previewArtifactId,
    status: incoming.status ?? existing.status,
  }
}

function collectPreviewRecords(
  slideDrafts: readonly SlideDraftItem[] | null | undefined,
  events: readonly JobEvent[],
  variantId: string | null | undefined,
): SlidePreviewRecord[] {
  const records: SlidePreviewRecord[] = []

  if (slideDrafts) {
    for (const draft of slideDrafts) {
      if (!matchesVariantId(draft.variant_id, variantId)) continue
      const slideKey = typeof draft.slide_key === 'string' && draft.slide_key.trim() ? draft.slide_key.trim() : null
      const index = typeof draft.index === 'number' && Number.isFinite(draft.index) ? draft.index : parseSlideKeyIndex(slideKey)
      const slideId = typeof draft.slide_id === 'string' && draft.slide_id.trim() ? draft.slide_id.trim() : null
      const previewArtifactId =
        typeof draft.preview_artifact_id === 'string' && draft.preview_artifact_id.trim() ? draft.preview_artifact_id.trim() : null
      const previewRevision = typeof draft.preview_revision === 'number' && Number.isFinite(draft.preview_revision) ? draft.preview_revision : null
      const revision = typeof draft.revision === 'number' && Number.isFinite(draft.revision) ? draft.revision : 1
      const status = typeof draft.status === 'string' && draft.status.trim() ? draft.status.trim() : null
      if (!previewArtifactId && status !== 'superseded' && status !== 'preview_failed' && previewRevision === null) continue
      records.push({
        slideKey,
        slideId,
        index,
        revision,
        previewRevision,
        previewArtifactId,
        status,
      })
    }
  }

  for (const event of events) {
    if (!SLIDE_EVENT_TYPES.has(event.type)) continue
    if (!matchesVariant(event, variantId)) continue
    const slideKey = readString(event, 'slide_key')
    const index = readNumber(event, 'index') ?? parseSlideKeyIndex(slideKey)
    const slideId = readString(event, 'slide_id')
    const previewArtifactId = readString(event, 'preview_artifact_id') ?? readString(event, 'image_artifact_id')
    const previewRevision = readNumber(event, 'preview_revision')
    const revision = readNumber(event, 'revision') ?? 1
    const status = readString(event, 'status') ?? (event.type === 'slide.preview_failed' ? 'preview_failed' : null)
    if (!previewArtifactId && status !== 'superseded' && status !== 'preview_failed') continue
    records.push({
      slideKey,
      slideId,
      index,
      revision,
      previewRevision,
      previewArtifactId,
      status,
    })
  }

  return records
}

function resolvePreviewBadge(
  record: SlidePreviewRecord | undefined,
  variantState: VariantCardState,
): SlidePreviewBadge {
  if (!record) return null
  if (record.status === 'superseded') return 'обновляется'
  if (record.previewRevision !== null && record.previewRevision >= 2) return null
  if (record.status === 'rendered' && record.previewRevision !== 1) return null
  if (record.previewRevision === 1 || record.status === 'draft') {
    return variantState === 'done' && record.previewRevision === null ? null : 'черновик'
  }
  return null
}

function extractBullets(planSlide: SlidePlan | undefined): string[] {
  if (!planSlide) return []
  const titleText = planSlide.title_intent?.trim().toLowerCase() ?? ''
  const lines: string[] = []
  for (const unit of planSlide.content_units ?? []) {
    const text = unit.text?.trim()
    if (!text) continue
    if (text.toLowerCase() === titleText) continue
    if (!lines.includes(text)) {
      lines.push(text)
    }
    if (lines.length >= 4) break
  }
  if (lines.length === 0 && planSlide.key_message?.trim()) {
    const msg = planSlide.key_message.trim()
    if (msg.toLowerCase() !== titleText) {
      lines.push(msg)
    }
  }
  return lines
}

export function buildFilmstripSlots({
  plan,
  variantSlides,
  slideDrafts,
  events = [],
  variantId,
  variantState = 'running',
}: BuildFilmstripSlotsOptions): FilmstripSlot[] {
  const planSlides: readonly SlidePlan[] = plan?.slides ?? []
  const slides: readonly SlideInfo[] = variantSlides ?? []
  const records = collectPreviewRecords(slideDrafts, events, variantId)

  const recordsZeroBased = records.some((item) => item.index === 0)
  const slidesZeroBased = slides.some((item) => item.index === 0)

  const recordBySlideKey = new Map<string, SlidePreviewRecord>()
  const recordBySlideId = new Map<string, SlidePreviewRecord>()
  const recordBySlotIndex = new Map<number, SlidePreviewRecord>()
  const idsByVariant = new Map<string, Set<string>>()

  let maxRecordSlot = -1
  for (const item of records) {
    if (item.slideKey) {
      recordBySlideKey.set(item.slideKey, mergePreviewRecord(recordBySlideKey.get(item.slideKey), item))
      if (item.slideKey.includes(':')) {
        const suffixKey = item.slideKey.slice(item.slideKey.lastIndexOf(':'))
        recordBySlideKey.set(suffixKey, mergePreviewRecord(recordBySlideKey.get(suffixKey), item))
      }
    }
    if (item.slideId) {
      recordBySlideId.set(item.slideId, mergePreviewRecord(recordBySlideId.get(item.slideId), item))
    }
    if (item.index !== null) {
      const slotIdx = recordsZeroBased ? item.index : Math.max(0, item.index - 1)
      if (slotIdx > maxRecordSlot) maxRecordSlot = slotIdx
      recordBySlotIndex.set(slotIdx, mergePreviewRecord(recordBySlotIndex.get(slotIdx), item))
    }
    const variantPrefix = item.slideKey?.includes(':') ? item.slideKey.slice(0, item.slideKey.indexOf(':')) : ''
    const perDeckKey =
      item.index !== null
        ? String(item.index)
        : item.slideKey?.includes(':')
          ? item.slideKey.slice(item.slideKey.lastIndexOf(':') + 1)
          : (item.slideId ?? item.slideKey ?? '')
    if (perDeckKey) {
      const set = idsByVariant.get(variantPrefix) ?? new Set<string>()
      set.add(perDeckKey)
      idsByVariant.set(variantPrefix, set)
    }
  }

  const maxVariantRecords = Math.max(0, ...[...idsByVariant.values()].map((set) => set.size))
  const totalCount = Math.max(planSlides.length, slides.length, maxRecordSlot + 1, maxVariantRecords)
  if (totalCount === 0) return []

  const variantSlideByPlanId = new Map<string, SlideInfo>()
  const variantSlideBySlotIndex = new Map<number, SlideInfo>()
  for (const slide of slides) {
    if (slide.slide_plan_id) {
      variantSlideByPlanId.set(slide.slide_plan_id, slide)
    }
    const slotIdx = slidesZeroBased ? slide.index : Math.max(0, slide.index - 1)
    variantSlideBySlotIndex.set(slotIdx, slide)
  }

  let markedBuilding = false

  return Array.from({ length: totalCount }, (_, index): FilmstripSlot => {
    const slideNumber = index + 1
    const planSlide = planSlides[index]
    const slideId = planSlide?.id ?? `slide-${index}`
    const canonicalSlideKey = variantId ? `${variantId}:${slideNumber}` : `:${slideNumber}`

    const variantSlide =
      (planSlide ? variantSlideByPlanId.get(planSlide.id) : undefined) ??
      variantSlideBySlotIndex.get(index) ??
      slides[index]

    const record =
      recordBySlideKey.get(canonicalSlideKey) ??
      (planSlide ? recordBySlideId.get(planSlide.id) : undefined) ??
      recordBySlotIndex.get(index)

    const previewArtifactId = record?.previewArtifactId ?? variantSlide?.preview_artifact_id ?? null
    const previewBadge = resolvePreviewBadge(record, variantState)
    const imageUrl = previewArtifactId ? artifactUrl(previewArtifactId) : null
    const isReady = Boolean(imageUrl) || variantState === 'done'

    let state: FilmstripSlotState = 'draft'
    if (isReady) {
      state = 'ready'
    } else if (!markedBuilding && (variantState === 'running' || variantState === 'pending' || variantState === 'queued')) {
      state = 'building'
      markedBuilding = true
    }

    const title =
      planSlide?.title_intent?.trim() ||
      planSlide?.key_message?.trim() ||
      variantSlide?.title?.trim() ||
      `Слайд ${formatIndex(slideNumber)}`
    const purpose = planSlide?.purpose ?? variantSlide?.purpose ?? null

    const rawLayout = (variantSlide as Record<string, unknown> | undefined)?.layout_name
    const layoutName = typeof rawLayout === 'string' && rawLayout.trim() ? rawLayout.trim() : null

    return {
      key: record?.slideKey ?? planSlide?.id ?? variantSlide?.id ?? `slot-${index}`,
      slideKey: record?.slideKey ?? canonicalSlideKey,
      index,
      slideNumber,
      slideId,
      title,
      keyMessage: planSlide?.key_message?.trim() ?? null,
      bullets: extractBullets(planSlide),
      purposeLabel: slidePurposeLabel(purpose),
      layoutName,
      previewArtifactId,
      previewRevision: record?.previewRevision ?? (variantSlide?.preview_artifact_id ? 2 : null),
      previewStatus: record?.status ?? (variantSlide?.preview_artifact_id ? 'rendered' : null),
      previewBadge,
      imageUrl,
      state,
    }
  })
}

export function buildStageSummary({
  phase,
  stage,
  cardState,
  slots,
  hasPlan,
}: {
  phase: GenerationPhase
  stage?: string | null
  cardState?: VariantCardState
  slots: readonly FilmstripSlot[]
  hasPlan: boolean
}): BuildStageSummary {
  const totalSlides = slots.length
  const readySlides = Math.min(totalSlides, slots.filter((slot) => Boolean(slot.imageUrl) || slot.state === 'ready').length)
  const draftSlides = slots.filter((slot) => slot.previewBadge === 'черновик').length
  const updatingSlides = slots.filter((slot) => slot.previewBadge === 'обновляется').length
  const knownStage = pipelineStage(stage)
  const isCompleted = phase === 'completed' || cardState === 'done'
  const isFailed = phase === 'failed' || cardState === 'error'

  const isPlanActive =
    !isCompleted &&
    readySlides === 0 &&
    !hasPlan &&
    (phase === 'submitting' || knownStage === 'template' || knownStage === 'content' || knownStage === 'plan')

  const planState: BuildStageItemState = isPlanActive ? (isFailed ? 'pending' : 'active') : 'done'
  const planStatusLabel = planState === 'done' ? 'готово' : isFailed ? 'остановлено' : 'собираю'

  const isAuditStage = knownStage === 'audit' || knownStage === 'repair' || knownStage === 'passport'
  const allSlidesHavePreview = totalSlides > 0 && readySlides >= totalSlides
  const allSlidesReady = allSlidesHavePreview && draftSlides === 0 && updatingSlides === 0

  let slidesState: BuildStageItemState = 'pending'
  if (isCompleted || (isAuditStage && allSlidesReady) || (isFailed && allSlidesHavePreview)) {
    slidesState = 'done'
  } else if (planState === 'done' && !isFailed) {
    slidesState = 'active'
  }

  let slidesStatusLabel = 'ожидает'
  if (slidesState === 'done') {
    slidesStatusLabel = isFailed && draftSlides > 0 ? 'черновик' : 'готово'
  } else if (isFailed && planState === 'done') {
    slidesStatusLabel = readySlides > 0 ? 'черновик' : 'остановлено'
  } else if (slidesState === 'active') {
    if (updatingSlides > 0) {
      slidesStatusLabel = 'обновляется'
    } else if (draftSlides > 0) {
      slidesStatusLabel = 'черновик'
    } else {
      slidesStatusLabel = 'собираю'
    }
  }

  let auditState: BuildStageItemState = 'pending'
  if (isCompleted) {
    auditState = 'done'
  } else if (!isFailed && (isAuditStage || (planState === 'done' && allSlidesHavePreview))) {
    auditState = 'active'
  }

  const auditStatusLabel =
    auditState === 'done'
      ? 'готово'
      : isFailed && (isAuditStage || allSlidesHavePreview)
        ? 'сбой'
        : auditState === 'active'
          ? 'проверяю'
          : 'ожидает'
  const slidesLabel = totalSlides > 0 ? `Слайды ${readySlides} из ${totalSlides}` : 'Слайды'

  return {
    stages: [
      { id: 'plan', label: 'План', statusLabel: planStatusLabel, state: planState },
      { id: 'slides', label: slidesLabel, statusLabel: slidesStatusLabel, state: slidesState },
      { id: 'audit', label: 'Проверка', statusLabel: auditStatusLabel, state: auditState },
    ],
    readySlides,
    totalSlides,
    draftSlides,
    updatingSlides,
  }
}

function clampNoteProgress(note: string, totalSlides?: number): string {
  return note.replace(/(\d+)\s+из\s+(\d+)/g, (_match, rawDone: string, rawTotal: string) => {
    const parsedDone = Number.parseInt(rawDone, 10)
    const parsedTotal = Number.parseInt(rawTotal, 10)
    const total = totalSlides && totalSlides > 0 ? totalSlides : parsedTotal
    if (!Number.isFinite(parsedDone) || !Number.isFinite(total) || total <= 0) return `${rawDone} из ${rawTotal}`
    return `${Math.min(parsedDone, total)} из ${total}`
  })
}

export function latestAssistantNote(
  events: readonly JobEvent[],
  variantId?: string | null,
  totalSlides?: number,
): string | null {
  if (events.length === 0) return null

  const readyByVariant = new Map<string, Set<string>>()
  for (const event of events) {
    if (event.type !== 'slide.ready') continue
    if (!matchesVariant(event, variantId)) continue
    const previewArtifactId = readString(event, 'preview_artifact_id') ?? readString(event, 'image_artifact_id')
    if (!previewArtifactId) continue
    const rawKey = readString(event, 'slide_key') ?? readString(event, 'slide_id') ?? String(readNumber(event, 'index') ?? event.sequence)
    const variantKey = readString(event, 'variant_id') ?? (rawKey.includes(':') ? rawKey.slice(0, rawKey.indexOf(':')) : '')
    const slideIndex = readNumber(event, 'index') ?? parseSlideKeyIndex(rawKey)
    const slotKey = slideIndex !== null ? String(slideIndex) : (rawKey.includes(':') ? rawKey.slice(rawKey.lastIndexOf(':') + 1) : rawKey)
    const slots = readyByVariant.get(variantKey) ?? new Set<string>()
    slots.add(slotKey)
    readyByVariant.set(variantKey, slots)
  }
  const rawReadyCount = Math.max(0, ...[...readyByVariant.values()].map((slots) => slots.size))
  const readyCount = totalSlides && totalSlides > 0 ? Math.min(rawReadyCount, totalSlides) : rawReadyCount

  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (!event) continue
    if (!matchesVariant(event, variantId)) continue
    const note = readString(event, 'assistant_note')
    if (note) return clampNoteProgress(note, totalSlides)
    if (event.type === 'slide.ready' && readyCount > 0) {
      const total = totalSlides && totalSlides > 0 ? totalSlides : readyCount
      return `Собираю ${Math.min(readyCount, total)} из ${total}…`
    }
  }

  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (!event) continue
    const note = readString(event, 'assistant_note')
    if (note) return clampNoteProgress(note, totalSlides)
  }

  return null
}

export function extractEventGenerationId(events: readonly JobEvent[]): string | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (!event) continue
    const genId = readString(event, 'generation_id')
    if (genId) return genId
  }
  return null
}
