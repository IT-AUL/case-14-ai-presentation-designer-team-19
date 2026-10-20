import type { AssistantSession, JobEvent, PreviewStyle, StylePreview } from '@/entities/assistant'
import { slidePurposeLabel, type DeckPlan, type GenerationBrief, type GenerationCreate, type SlidePlan } from '@/entities/generation'
import type { Project } from '@/entities/project'
import { DEFAULT_STRATEGY, STRATEGY_ORDER, strategyInfo, type CatalogStrategy } from '@/entities/variant'
import { prepareForBuild, type StoredPlanRequest } from '@/features/edit-deck-plan'
import { artifactUrl, humanError } from '@/shared/api'
import { formatIndex } from '@/shared/lib/format'

export type StyleSlotState = 'loading' | 'ready' | 'draft' | 'error'

export type BuildExecutionMode = 'fast' | 'checked'

export interface BuildModeOptionView {
  mode: BuildExecutionMode
  label: string
  summary: string
}

export const BUILD_MODE_OPTIONS: readonly BuildModeOptionView[] = [
  {
    mode: 'fast',
    label: 'Быстро',
    summary: 'Быстро — соберёт колоду за минуту без промежуточной проверки',
  },
  {
    mode: 'checked',
    label: 'С проверкой',
    summary: 'С проверкой — проверит числа, источники и плотность каждого слайда',
  },
]

export function resolveBuildModeSummary(mode: BuildExecutionMode): string {
  return mode === 'fast'
    ? 'Быстро — соберёт колоду за минуту без промежуточной проверки'
    : 'С проверкой — проверит числа, источники и плотность каждого слайда'
}

export interface StyleSlideSlotView {
  slideId: string
  slideNumber: number
  title: string
  purposeLabel: string | null
  exemplar: string | null
  imageArtifactId: string | null
  imageUrl: string | null
  state: StyleSlotState
  loadingCaption: string
}

export interface StyleColumnView {
  strategy: CatalogStrategy
  title: string
  difference: string
  recommended: boolean
  seconds: number | null
  error: string | null
  slots: StyleSlideSlotView[]
}

const FALLBACK_STYLE_TITLES: Record<CatalogStrategy, string> = {
  faithful: 'Близко к шаблону',
  balanced: 'Сбалансированный',
  visual: 'Визуальный',
}

const STRATEGY_ALIAS_TITLES: Record<string, string> = {
  faithful: 'Близко к шаблону',
  strict: 'Близко к шаблону',
  balanced: 'Сбалансированный',
  visual: 'Визуальный',
  creative: 'Визуальный',
}

const LEGACY_BACKEND_TITLES: Record<string, string> = {
  Строгий: 'Близко к шаблону',
  строгий: 'Близко к шаблону',
  Визуальный: 'Визуальный',
  визуальный: 'Визуальный',
}

const FALLBACK_STYLE_DIFFERENCES: Record<CatalogStrategy, string> = {
  faithful: 'Ближе всего к вашему шаблону: его привычные раскладки, спокойная плотность текста.',
  balanced: 'Другие подходящие раскладки шаблона, текст и визуал поровну.',
  visual: 'Больше графиков и схем, меньше текста, смелее группировка.',
}

const STRATEGY_ALIAS_DIFFERENCES: Record<string, string> = {
  faithful: FALLBACK_STYLE_DIFFERENCES.faithful,
  strict: FALLBACK_STYLE_DIFFERENCES.faithful,
  balanced: FALLBACK_STYLE_DIFFERENCES.balanced,
  visual: FALLBACK_STYLE_DIFFERENCES.visual,
  creative: FALLBACK_STYLE_DIFFERENCES.visual,
}

export function fallbackStyleTitle(strategy: string): string {
  const normalized = strategy.trim().toLowerCase()
  if (normalized in STRATEGY_ALIAS_TITLES) {
    return STRATEGY_ALIAS_TITLES[normalized] ?? strategyInfo(strategy).name
  }
  if (strategy in FALLBACK_STYLE_TITLES) return FALLBACK_STYLE_TITLES[strategy as CatalogStrategy]
  return strategyInfo(strategy).name
}

export function resolveStyleTitle(strategy: string, backendTitle?: string | null): string {
  const trimmed = backendTitle?.trim()
  if (trimmed && trimmed in LEGACY_BACKEND_TITLES) {
    return LEGACY_BACKEND_TITLES[trimmed] ?? fallbackStyleTitle(strategy)
  }
  const normalized = strategy.trim().toLowerCase()
  if (normalized in STRATEGY_ALIAS_TITLES) {
    return STRATEGY_ALIAS_TITLES[normalized] ?? fallbackStyleTitle(strategy)
  }
  return trimmed || fallbackStyleTitle(strategy)
}

export function fallbackStyleDifference(strategy: string): string {
  const normalized = strategy.trim().toLowerCase()
  if (normalized in STRATEGY_ALIAS_DIFFERENCES) {
    return STRATEGY_ALIAS_DIFFERENCES[normalized] ?? strategyInfo(strategy).axis
  }
  if (strategy in FALLBACK_STYLE_DIFFERENCES) return FALLBACK_STYLE_DIFFERENCES[strategy as CatalogStrategy]
  return strategyInfo(strategy).axis
}

export function formatExemplarLabel(exemplar: string | null | undefined): string | null {
  if (!exemplar) return null
  const trimmed = exemplar.trim()
  if (!trimmed) return null
  const match = /slide(\d+)(?:\.xml)?$/i.exec(trimmed)
  if (match?.[1]) return `Образец: слайд #${match[1]}`
  return `Образец: ${trimmed}`
}

function hasVisualData(slide: SlidePlan): boolean {
  return (
    slide.purpose === 'data' ||
    Boolean(slide.chart) ||
    slide.desired_visual === 'chart' ||
    slide.desired_visual === 'table' ||
    slide.desired_visual === 'diagram' ||
    slide.content_units.some((unit) => unit.kind === 'chart' || Boolean(unit.chart_ref))
  )
}

export function slotLoadingCaption(slide: SlidePlan | undefined, position: number): string {
  if (position === 0 || slide?.purpose === 'title') return 'Верстаю обложку…'
  if (slide && hasVisualData(slide)) {
    return 'Верстаю слайд с графиком…'
  }
  if (slide?.desired_visual === 'table') return 'Верстаю слайд с таблицей…'
  if (slide?.desired_visual === 'diagram') return 'Верстаю слайд со схемой…'
  return 'Верстаю текстовый слайд…'
}

export function pickKeySlideIds(plan: DeckPlan | null | undefined, limit = 3): string[] {
  const slides = plan?.slides ?? []
  if (slides.length === 0) return ['slide-0', 'slide-1', 'slide-2'].slice(0, limit)
  const picked: string[] = []
  const first = slides[0]
  if (first) picked.push(first.id)
  const dataSlide = slides.find((slide) => !picked.includes(slide.id) && hasVisualData(slide))
  if (dataSlide) picked.push(dataSlide.id)
  const textSlide =
    slides.find(
      (slide) =>
        !picked.includes(slide.id) &&
        slide.purpose !== 'thank_you' &&
        slide.content_units.filter((unit) => unit.kind === 'bullet' || unit.kind === 'paragraph').length >= 2,
    ) ?? slides.find((slide) => !picked.includes(slide.id) && slide.purpose !== 'thank_you')
  if (textSlide) picked.push(textSlide.id)
  for (const slide of slides) {
    if (picked.length >= limit) break
    if (!picked.includes(slide.id)) picked.push(slide.id)
  }
  return picked.slice(0, limit)
}

export function extractPreviewReadyMap(events: readonly JobEvent[]): ReadonlyMap<string, string> {
  const map = new Map<string, string>()
  for (const event of events) {
    if (event.type !== 'preview.ready') continue
    const strategy = typeof event.data.strategy === 'string' ? event.data.strategy : typeof event.strategy === 'string' ? event.strategy : null
    const slideId = typeof event.data.slide_id === 'string' ? event.data.slide_id : typeof event.slide_id === 'string' ? event.slide_id : null
    const artifactId =
      typeof event.data.image_artifact_id === 'string'
        ? event.data.image_artifact_id
        : typeof event.image_artifact_id === 'string'
          ? event.image_artifact_id
          : null
    if (strategy && slideId && artifactId) {
      map.set(`${strategy}:${slideId}`, artifactId)
    }
  }
  return map
}

export function extractCompletedEventMeta(events: readonly JobEvent[]): { recommended: string | null; assistantNote: string | null } {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (!event) continue
    if (event.type === 'job.completed') {
      const recommended =
        typeof event.data.recommended === 'string' ? event.data.recommended : typeof event.recommended === 'string' ? event.recommended : null
      const assistantNote =
        typeof event.data.assistant_note === 'string'
          ? event.data.assistant_note
          : typeof event.assistant_note === 'string'
            ? event.assistant_note
            : null
      return { recommended, assistantNote }
    }
  }
  return { recommended: null, assistantNote: null }
}

function resolveSlideOrder(
  preview: StylePreview | null | undefined,
  events: readonly JobEvent[],
  plan: DeckPlan | null | undefined,
): string[] {
  const planSlides = plan?.slides ?? []
  const planIndexById = new Map(planSlides.map((slide, index) => [slide.id, slide.index ?? index]))
  const previewIndexById = new Map<string, number>()
  for (const style of preview?.styles ?? []) {
    for (const item of style.previews) {
      if (typeof item.index === 'number' && !previewIndexById.has(item.slide_id)) {
        previewIndexById.set(item.slide_id, item.index)
      }
    }
  }
  const rawIds = new Set<string>()
  for (const id of preview?.slide_ids ?? []) rawIds.add(id)
  for (const style of preview?.styles ?? []) {
    for (const item of style.previews) rawIds.add(item.slide_id)
  }
  let ids: string[]
  if (rawIds.size > 0) {
    ids = [...rawIds]
  } else {
    const eventIds: string[] = []
    for (const event of events) {
      if (event.type === 'preview.ready') {
        const slideId = typeof event.data.slide_id === 'string' ? event.data.slide_id : null
        if (slideId && !eventIds.includes(slideId)) eventIds.push(slideId)
      }
    }
    ids = [...new Set([...pickKeySlideIds(plan), ...eventIds])]
  }
  return ids.sort((a, b) => {
    const idxA = planIndexById.get(a) ?? previewIndexById.get(a) ?? Number.MAX_SAFE_INTEGER
    const idxB = planIndexById.get(b) ?? previewIndexById.get(b) ?? Number.MAX_SAFE_INTEGER
    return idxA - idxB
  })
}

function errorMessage(error: unknown): string | null {
  if (!error) return null
  if (typeof error === 'string') return error
  if (error instanceof Error) return humanError(error, 'Не удалось отрисовать превью этого стиля')
  return 'Не удалось отрисовать превью этого стиля'
}

export interface BuildStyleColumnsOptions {
  preview: StylePreview | null | undefined
  events: readonly JobEvent[]
  isStreaming: boolean
  plan: DeckPlan | null | undefined
  unsupported?: boolean
}

export function buildStyleColumns({
  preview,
  events,
  isStreaming,
  plan,
  unsupported = false,
}: BuildStyleColumnsOptions): StyleColumnView[] {
  const readyMap = extractPreviewReadyMap(events)
  const completedMeta = extractCompletedEventMeta(events)
  const slideIds = resolveSlideOrder(preview, events, plan)
  const planSlideById = new Map((plan?.slides ?? []).map((slide) => [slide.id, slide]))
  const styleByStrategy = new Map<string, PreviewStyle>((preview?.styles ?? []).map((style) => [style.strategy, style]))
  const recommendedStrategy =
    preview?.recommended ??
    completedMeta.recommended ??
    preview?.styles.find((style) => style.recommended)?.strategy ??
    DEFAULT_STRATEGY

  return STRATEGY_ORDER.map((strategy) => {
    const backendStyle = styleByStrategy.get(strategy)
    const title = resolveStyleTitle(strategy, backendStyle?.title)
    const difference = backendStyle?.difference?.trim() || fallbackStyleDifference(strategy)
    const recommended = backendStyle ? backendStyle.recommended || preview?.recommended === strategy : recommendedStrategy === strategy
    const styleError = errorMessage(backendStyle?.error)
    const previewBySlideId = new Map((backendStyle?.previews ?? []).map((item) => [item.slide_id, item]))
    const exemplarsRecord = backendStyle?.exemplars ?? {}

    const slots: StyleSlideSlotView[] = slideIds.map((slideId, position) => {
      const planSlide = planSlideById.get(slideId)
      const previewItem = previewBySlideId.get(slideId)
      const rawIndex = planSlide?.index ?? previewItem?.index ?? position
      const slideNumber = rawIndex + 1
      const slideTitle = planSlide?.title_intent?.trim() || planSlide?.key_message?.trim() || `Слайд ${formatIndex(slideNumber)}`
      const purposeLabel = slidePurposeLabel(planSlide?.purpose)
      const rawExemplar =
        previewItem?.exemplar ?? (typeof exemplarsRecord[slideId] === 'string' ? String(exemplarsRecord[slideId]) : null)
      const exemplar = formatExemplarLabel(rawExemplar)
      const fromEvent = readyMap.get(`${strategy}:${slideId}`) ?? null
      const imageArtifactId = isStreaming ? fromEvent : (fromEvent ?? previewItem?.image_artifact_id ?? null)
      const imageUrl = imageArtifactId ? artifactUrl(imageArtifactId) : null
      const state: StyleSlotState = imageUrl
        ? 'ready'
        : styleError
          ? 'error'
          : unsupported
            ? 'draft'
            : 'loading'

      return {
        slideId,
        slideNumber,
        title: slideTitle,
        purposeLabel,
        exemplar,
        imageArtifactId,
        imageUrl,
        state,
        loadingCaption: slotLoadingCaption(planSlide, position),
      }
    })

    return {
      strategy,
      title,
      difference,
      recommended,
      seconds: backendStyle?.seconds ?? null,
      error: styleError,
      slots,
    }
  })
}

export function resolveRecommendedStrategy(columns: readonly StyleColumnView[]): CatalogStrategy {
  return columns.find((column) => column.recommended)?.strategy ?? DEFAULT_STRATEGY
}

export interface ResolveStyleNoteOptions {
  preview: StylePreview | null | undefined
  events: readonly JobEvent[]
  sessionNote?: string | null
  sessionStep?: number | null
  isStreaming: boolean
  unsupported?: boolean
  hasPlan: boolean
  plan?: DeckPlan | null
}

const INTAKE_NOTE_PATTERN = /добавьт[её]\s+(корпоративн\w*\s+)?(шаблон|материалы|файлы)|принял:\s*шаблон|материалы принял/i

function staleSessionNote(note: string, sessionStep: number | null | undefined): boolean {
  if (typeof sessionStep === 'number' && sessionStep < 5) return true
  return INTAKE_NOTE_PATTERN.test(note)
}

export function resolveStyleAssistantNote({
  preview,
  events,
  sessionNote,
  sessionStep,
  isStreaming,
  unsupported = false,
  hasPlan,
  plan,
}: ResolveStyleNoteOptions): string {
  if (!hasPlan) {
    return 'Сначала составьте план презентации, чтобы я примерил три стиля на ваших слайдах.'
  }
  const previewNote = preview?.assistant_note?.trim()
  if (previewNote) return previewNote
  const completedMeta = extractCompletedEventMeta(events)
  if (completedMeta.assistantNote?.trim()) return completedMeta.assistantNote.trim()
  if (isStreaming) {
    const readyCount = extractPreviewReadyMap(events).size
    const total = resolveSlideOrder(preview, events, plan).length * STRATEGY_ORDER.length
    if (readyCount > 0) {
      return `Примерил ${readyCount} из ${total} пробных слайдов — сравните плотность текста и подачу графиков…`
    }
    return 'Примеряю три стиля вёрстки на ключевых слайдах вашего плана…'
  }
  if (unsupported) {
    return 'Выберите подходящий стиль вёрстки: в одном стиле соберём быстрее, во всех трёх — сравните полные колоды.'
  }
  const sessionText = sessionNote?.trim()
  if (sessionText && !staleSessionNote(sessionText, sessionStep)) return sessionText
  return 'Примерил три стиля вёрстки на ваших ключевых слайдах — сравните плотность текста и подачу графиков.'
}

const CANONICAL_BRIEF_FIELDS = ['purpose', 'audience', 'language', 'key_message', 'call_to_action'] as const

function sessionText(sessionBrief: Record<string, unknown> | null | undefined, field: string): string | null {
  const value = sessionBrief?.[field]
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function normalizeBrief(
  planBrief: DeckPlan['brief'] | null | undefined,
  storedBrief: GenerationBrief | null | undefined,
  sessionBrief: Record<string, unknown> | null | undefined,
  targetSlidesFallback: number,
): GenerationBrief {
  const source = planBrief ?? storedBrief
  const purpose = source?.purpose?.trim() || 'Презентация проекта'
  const audience = source?.audience !== undefined ? source.audience : 'Руководство'
  const language = source?.language?.trim() || 'ru'
  const targetSlideCount =
    source?.target_slide_count ??
    (typeof sessionBrief?.target_slide_count === 'number' ? sessionBrief.target_slide_count : targetSlidesFallback)

  const brief: GenerationBrief = {
    purpose,
    audience,
    language,
    target_slide_count: Math.max(3, Math.min(40, targetSlideCount || 8)),
    tone: source?.tone ?? (typeof sessionBrief?.tone === 'string' ? sessionBrief.tone : null),
    mandatory_sections: source?.mandatory_sections ?? null,
    forbidden_claims: source?.forbidden_claims ?? null,
    ...(source && 'content_fill' in source ? { content_fill: source.content_fill } : {}),
    ...(source && 'key_message' in source ? { key_message: source.key_message } : {}),
    ...(source && 'call_to_action' in source ? { call_to_action: source.call_to_action } : {}),
    ...(source && 'explain_slide' in source ? { explain_slide: source.explain_slide } : {}),
  }
  for (const field of CANONICAL_BRIEF_FIELDS) {
    const canonical = sessionText(sessionBrief, field)
    if (canonical) Object.assign(brief, { [field]: canonical })
  }
  return brief
}

export interface BuildGenerationPayloadOptions {
  stored: StoredPlanRequest | null
  plan: DeckPlan | null
  session: AssistantSession | null | undefined
  project: Project | null | undefined
  previewId: string | null
  strategies: readonly CatalogStrategy[]
  executionMode?: BuildExecutionMode
  modelAvailable?: boolean
  providerSessionId?: string | null
}

export function buildGenerationPayload({
  stored,
  plan,
  session,
  project,
  previewId,
  strategies,
  executionMode,
  modelAvailable,
  providerSessionId,
}: BuildGenerationPayloadOptions): GenerationCreate | null {
  const templateId =
    stored?.request.template_id ??
    session?.artifacts.template_id ??
    project?.template_id ??
    null
  const packFromGraph = plan?.evidence_graph_id?.startsWith('pack:') ? plan.evidence_graph_id.slice('pack:'.length) : null
  const contentPackId =
    packFromGraph ??
    stored?.request.content_pack_id ??
    session?.artifacts.content_pack_id ??
    project?.content_pack_id ??
    null

  if (!templateId || !contentPackId) return null

  const preparedPlan = plan ? prepareForBuild(plan, { lockAll: stored?.contentFill === 'keep' }) : null
  const slideCount = preparedPlan?.slides.length ?? project?.target_slide_count ?? 8
  const brief = normalizeBrief(preparedPlan?.brief, stored?.request.brief, session?.brief, slideCount)
  const syncedPlan = preparedPlan ? { ...preparedPlan, brief } : null
  const planId = stored?.result.planId ?? session?.artifacts.plan_id ?? null
  const chosenStrategies = strategies.length > 0 ? strategies : [DEFAULT_STRATEGY]
  const useLlm = executionMode === 'fast' ? false : (modelAvailable || Boolean(providerSessionId) || Boolean(stored?.request.use_llm))

  return {
    template_id: templateId,
    content_pack_id: contentPackId,
    provider_session_id: providerSessionId ?? stored?.request.provider_session_id ?? null,
    brief,
    variants: chosenStrategies.map((strategy) => ({ strategy })),
    use_llm: useLlm,
    ...(executionMode ? { execution_mode: executionMode } : {}),
    ...(syncedPlan ? { deck_plan: syncedPlan } : planId ? { deck_plan_id: planId } : {}),
    ...(previewId ? { preview_id: previewId } : {}),
    ...(session?.artifacts.brandbook_id ? { brandbook_id: session.artifacts.brandbook_id } : {}),
  }
}
