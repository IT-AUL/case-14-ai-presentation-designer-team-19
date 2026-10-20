import { isEditableUnit, type ContentUnit, type DeckPlan, type SlidePlan } from '@/entities/generation'

export type QuickSlideAction = 'shorten' | 'split' | 'merge_next' | 'to_chart' | 'to_diagram' | 'remove'

export interface SlideChartAlternative {
  type: string
  reasons: string[]
}

export interface SlideChartInfo {
  type: string
  reasons: string[]
  alternatives: SlideChartAlternative[]
  viewId: string | null
  why: string | null
  previewArtifactId: string | null
}

export interface SlidePatch {
  title_intent?: string
  key_message?: string
  desired_visual?: SlidePlan['desired_visual']
  supports_key_message?: boolean | null
}

export type EditableUnit = ContentUnit

const BOOKEND_PURPOSES = new Set(['title', 'thank_you', 'cta', 'agenda'])
const OVERVIEW_PURPOSES = new Set([...BOOKEND_PURPOSES, 'overview'])
const WORD_PATTERN = /[a-zа-яё]{4,}/gi
const STOP_WORDS = new Set(['этот', 'этого', 'также', 'который', 'которые', 'чтобы', 'after', 'with', 'that'])

const CHART_TITLE_RU: Record<string, string> = {
  line: 'Линия',
  bar: 'Столбцы',
  bar_horizontal: 'Горизонтальные столбцы',
  bar_stacked: 'Накопительные столбцы',
  bar_stacked_100: 'Столбцы 100%',
  histogram: 'Гистограмма',
  pie: 'Круговая',
  doughnut: 'Кольцевая',
  donut: 'Кольцевая',
  scatter: 'Точечная',
  kpi: 'Карточки с числами',
  table: 'Таблица',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0) : []
}

export function parseSlideChart(raw: SlidePlan['chart']): SlideChartInfo | null {
  if (!isRecord(raw) || typeof raw.type !== 'string' || !raw.type.trim()) return null
  const alternatives: SlideChartAlternative[] = Array.isArray(raw.alternatives)
    ? raw.alternatives
        .filter(isRecord)
        .filter((item) => typeof item.type === 'string' && item.type.trim().length > 0)
        .map((item) => ({ type: String(item.type), reasons: stringList(item.reasons) }))
    : []
  return {
    type: raw.type,
    reasons: stringList(raw.reasons),
    alternatives,
    viewId: typeof raw.view_id === 'string' ? raw.view_id : null,
    why: typeof raw.why === 'string' && raw.why.trim() ? raw.why.trim() : null,
    previewArtifactId: typeof raw.preview_artifact_id === 'string' ? raw.preview_artifact_id : null,
  }
}

function ordered(plan: DeckPlan): SlidePlan[] {
  return [...plan.slides].sort((a, b) => a.index - b.index)
}

function withSlides(plan: DeckPlan, slides: readonly SlidePlan[]): DeckPlan {
  const ids = new Set(slides.map((slide) => slide.id))
  return {
    ...plan,
    brief: { ...plan.brief, target_slide_count: slides.length },
    slides: slides.map((slide, index) => (slide.index === index ? slide : { ...slide, index })),
    sections: plan.sections?.map((section) => ({ ...section, slide_ids: section.slide_ids.filter((id) => ids.has(id)) })),
  }
}

function mapSlide(plan: DeckPlan, slideId: string, edit: (slide: SlidePlan) => SlidePlan): DeckPlan {
  let changed = false
  const slides = ordered(plan).map((slide) => {
    if (slide.id !== slideId) return slide
    const next = edit(slide)
    if (next !== slide) changed = true
    return next
  })
  return changed ? withSlides(plan, slides) : plan
}

export function moveSlide(plan: DeckPlan, slideId: string, delta: -1 | 1): DeckPlan {
  const slides = ordered(plan)
  const from = slides.findIndex((slide) => slide.id === slideId)
  const to = from + delta
  if (from < 0 || to < 0 || to >= slides.length) return plan
  const next = [...slides]
  const [moved] = next.splice(from, 1)
  if (moved) next.splice(to, 0, moved)
  return withSlides(plan, next)
}

export function removeSlide(plan: DeckPlan, slideId: string): DeckPlan {
  const slides = ordered(plan)
  if (slides.length <= 1 || !slides.some((slide) => slide.id === slideId)) return plan
  return withSlides(
    plan,
    slides.filter((slide) => slide.id !== slideId),
  )
}

export function updateSlide(plan: DeckPlan, slideId: string, patch: SlidePatch): DeckPlan {
  return mapSlide(plan, slideId, (slide) => {
    const next = { ...slide, ...patch }
    const changed =
      next.title_intent !== slide.title_intent ||
      next.key_message !== slide.key_message ||
      next.desired_visual !== slide.desired_visual ||
      next.supports_key_message !== slide.supports_key_message
    if (!changed) return slide
    if (next.title_intent !== slide.title_intent) {
      next.content_units = slide.content_units.map((unit) =>
        unit.kind === 'title' ? { ...unit, text: next.title_intent, locked: true } : unit,
      )
    }
    return next
  })
}

function fallbackSlideChart(slide: SlidePlan): SlideChartInfo | null {
  const unit = slide.content_units.find((item) => isRecord(item.chart_spec) || item.kind === 'chart' || Boolean(item.chart_ref))
  const rawChartType = unit?.chart_spec?.chart_type
  const specType =
    (typeof rawChartType === 'string' && rawChartType.trim() ? rawChartType.trim() : null) ??
    (slide.purpose === 'data' ? 'bar' : null)
  if (!specType) return null
  return {
    type: specType,
    reasons: ['по структуре данных'],
    alternatives: [
      { type: specType === 'line' ? 'bar' : 'line', reasons: ['альтернативное представление'] },
      { type: 'table', reasons: ['точные цифры по строкам'] },
    ],
    viewId: null,
    why: null,
    previewArtifactId: null,
  }
}

export function changeSlideChart(plan: DeckPlan, slideId: string, chartType: string): DeckPlan {
  return mapSlide(plan, slideId, (slide) => {
    const current = parseSlideChart(slide.chart) ?? fallbackSlideChart(slide)
    if (!current || current.type === chartType) return slide
    const picked = current.alternatives.find((alt) => alt.type === chartType)
    const nextReasons = picked?.reasons.length ? picked.reasons : ['выбрано пользователем']
    const nextAlternatives: SlideChartAlternative[] = [
      { type: current.type, reasons: current.reasons },
      ...current.alternatives.filter((alt) => alt.type !== chartType && alt.type !== current.type),
    ]
    const label = CHART_TITLE_RU[chartType] ?? chartType
    const nextWhy = `${label}: ${nextReasons.join(', ')}.`
    const nextChart: Record<string, unknown> = {
      ...(isRecord(slide.chart) ? slide.chart : {}),
      type: chartType,
      reasons: nextReasons,
      alternatives: nextAlternatives,
      why: nextWhy,
    }
    const desiredVisual: SlidePlan['desired_visual'] = chartType === 'table' ? 'table' : 'chart'
    const units = slide.content_units.map((unit) => {
      if (unit.kind !== 'chart' && unit.kind !== 'table') return unit
      if (chartType === 'table') {
        return { ...unit, role: 'table', kind: 'table' as const, chart_spec: null }
      }
      return {
        ...unit,
        role: 'chart',
        kind: 'chart' as const,
        chart_spec: {
          ...(unit.chart_spec ?? { series_count: 1, has_axis_labels: true, has_units: true, has_legend: false }),
          chart_type: chartType as NonNullable<ContentUnit['chart_spec']>['chart_type'],
        },
      }
    })
    return {
      ...slide,
      desired_visual: desiredVisual,
      chart: nextChart,
      content_units: units,
    }
  })
}

function shortClause(text: string): string {
  const firstPart = text.trim().split(/[.;:,(—–-]\s/)[0] ?? text.trim()
  const words = firstPart.split(/\s+/).filter(Boolean)
  return words.slice(0, 6).join(' ').replace(/[.,;:]+$/, '')
}

function editableTextUnits(slide: SlidePlan): ContentUnit[] {
  return slide.content_units.filter((unit) => isEditableUnit(unit) && Boolean((unit.text ?? '').trim()))
}

export function canApplyQuickAction(plan: DeckPlan, slideId: string, action: QuickSlideAction): boolean {
  const slides = ordered(plan)
  const index = slides.findIndex((item) => item.id === slideId)
  const slide = slides[index]
  if (!slide) return false
  const texts = editableTextUnits(slide)
  switch (action) {
    case 'remove':
      return slides.length > 1 && slide.purpose !== 'title'
    case 'shorten':
      return !BOOKEND_PURPOSES.has(slide.purpose) && texts.length > 0
    case 'split':
      return !BOOKEND_PURPOSES.has(slide.purpose) && texts.length >= 2
    case 'merge_next': {
      const next = slides[index + 1]
      return slide.purpose !== 'title' && Boolean(next && !BOOKEND_PURPOSES.has(next.purpose))
    }
    case 'to_chart':
      return !BOOKEND_PURPOSES.has(slide.purpose) && slide.desired_visual !== 'chart'
    case 'to_diagram':
      return !BOOKEND_PURPOSES.has(slide.purpose) && slide.desired_visual !== 'diagram' && texts.length >= 2
  }
}

export function shortenSlide(plan: DeckPlan, slideId: string): DeckPlan {
  return mapSlide(plan, slideId, (slide) => {
    const title = slide.content_units.filter((unit) => unit.kind === 'title')
    const texts = editableTextUnits(slide)
    const textSet = new Set(texts)
    const other = slide.content_units.filter((unit) => unit.kind !== 'title' && !textSet.has(unit))
    if (texts.length === 0) return slide
    const nextTexts =
      texts.length === 1
        ? texts.map((unit) => {
            const current = unit.text ?? ''
            const shortened = current.length > 60 ? shortClause(current) : current.split(/\s+/).slice(0, Math.max(3, Math.ceil(current.split(/\s+/).length / 2))).join(' ')
            return shortened !== current ? { ...unit, text: shortened, locked: true } : unit
          })
        : texts.slice(0, Math.max(1, Math.floor(texts.length / 2)))
    return { ...slide, content_units: [...title, ...other, ...nextTexts] }
  })
}

export function splitSlide(plan: DeckPlan, slideId: string): DeckPlan {
  const slides = ordered(plan)
  const index = slides.findIndex((item) => item.id === slideId)
  const slide = slides[index]
  if (!slide) return plan
  const title = slide.content_units.filter((unit) => unit.kind === 'title')
  const texts = editableTextUnits(slide)
  if (texts.length < 2) return plan
  const textSet = new Set(texts)
  const other = slide.content_units.filter((unit) => unit.kind !== 'title' && !textSet.has(unit))
  const half = Math.ceil(texts.length / 2)
  const taken = new Set(slides.map((item) => item.id))
  let counter = slides.length
  while (taken.has(`slide-${counter}`)) counter += 1
  const newId = `slide-${counter}`
  const contTitle = `${slide.title_intent} (продолжение)`
  const firstHalf: SlidePlan = {
    ...slide,
    content_units: [...title, ...other, ...texts.slice(0, half)],
  }
  const secondHalf: SlidePlan = {
    ...slide,
    id: newId,
    title_intent: contTitle,
    content_units: [
      { role: 'title', kind: 'title', text: contTitle, evidence_ids: slide.evidence_ids.slice(0, 1) },
      ...texts.slice(half),
    ],
    desired_visual: 'none',
    chart: null,
  }
  const next = [...slides]
  next.splice(index, 1, firstHalf, secondHalf)
  const sections = plan.sections?.map((section) => {
    if (!section.slide_ids.includes(slide.id)) return section
    const pos = section.slide_ids.indexOf(slide.id)
    const ids = [...section.slide_ids]
    ids.splice(pos + 1, 0, newId)
    return { ...section, slide_ids: ids }
  })
  return withSlides({ ...plan, sections }, next)
}

export function mergeNextSlide(plan: DeckPlan, slideId: string): DeckPlan {
  const slides = ordered(plan)
  const index = slides.findIndex((item) => item.id === slideId)
  const slide = slides[index]
  const next = slides[index + 1]
  if (!slide || !next || BOOKEND_PURPOSES.has(next.purpose)) return plan
  const extraUnits = next.content_units.filter((unit) => unit.kind !== 'title')
  const mergedEvidence = [...new Set([...slide.evidence_ids, ...next.evidence_ids])]
  const mergedInsights = [...(slide.insight_ids ?? []), ...(next.insight_ids ?? [])]
  const merged: SlidePlan = {
    ...slide,
    content_units: [...slide.content_units, ...extraUnits],
    evidence_ids: mergedEvidence,
    insight_ids: mergedInsights.length > 0 ? mergedInsights : null,
  }
  const updated = slides.filter((_, position) => position !== index + 1).map((item, position) => (position === index ? merged : item))
  return withSlides(plan, updated)
}

export function toChartSlide(plan: DeckPlan, slideId: string): DeckPlan {
  return updateSlide(plan, slideId, { desired_visual: 'chart' })
}

export function toDiagramSlide(plan: DeckPlan, slideId: string): DeckPlan {
  return mapSlide(plan, slideId, (slide) => {
    const texts = editableTextUnits(slide)
    if (texts.length < 2) return slide
    return { ...slide, desired_visual: 'diagram' }
  })
}

export function applyQuickAction(plan: DeckPlan, slideId: string, action: QuickSlideAction): DeckPlan {
  switch (action) {
    case 'shorten':
      return shortenSlide(plan, slideId)
    case 'split':
      return splitSlide(plan, slideId)
    case 'merge_next':
      return mergeNextSlide(plan, slideId)
    case 'to_chart':
      return toChartSlide(plan, slideId)
    case 'to_diagram':
      return toDiagramSlide(plan, slideId)
    case 'remove':
      return removeSlide(plan, slideId)
  }
}

function stemsOf(text: string): Set<string> {
  const matches = text.match(WORD_PATTERN) ?? []
  const result = new Set<string>()
  for (const word of matches) {
    const lower = word.toLowerCase()
    if (!STOP_WORDS.has(lower)) result.add(lower.slice(0, 5))
  }
  return result
}

function slideTextForKeyMessage(slide: SlidePlan): string {
  return [slide.title_intent, slide.key_message, ...slide.content_units.map((unit) => unit.text ?? '')].join(' ')
}

export function annotateKeyMessageSupport(plan: DeckPlan, keyMessage: string): DeckPlan {
  const trimmed = keyMessage.trim()
  if (!trimmed) {
    return {
      ...plan,
      brief: { ...plan.brief, key_message: null },
      slides: ordered(plan).map((slide) => ({ ...slide, supports_key_message: null })),
    }
  }
  const keyStems = stemsOf(trimmed)
  const slides = ordered(plan).map((slide, position) => {
    if (OVERVIEW_PURPOSES.has(slide.purpose) || position === 0) {
      return { ...slide, supports_key_message: true }
    }
    if (keyStems.size === 0) {
      return { ...slide, supports_key_message: true }
    }
    const slideStems = stemsOf(slideTextForKeyMessage(slide))
    let overlaps = false
    for (const stem of keyStems) {
      if (slideStems.has(stem)) {
        overlaps = true
        break
      }
    }
    return { ...slide, supports_key_message: overlaps }
  })
  return {
    ...plan,
    brief: { ...plan.brief, key_message: trimmed },
    slides,
  }
}

export function updateKeyMessage(plan: DeckPlan, keyMessage: string): DeckPlan {
  const annotated = annotateKeyMessageSupport(plan, keyMessage)
  const trimmed = keyMessage.trim()
  if (!trimmed) return annotated
  const slides = annotated.slides.map((slide, index) => {
    if (index === 0 && slide.purpose === 'title') {
      return { ...slide, key_message: trimmed }
    }
    return slide
  })
  return { ...annotated, slides }
}

export function keepSlideOnTopic(plan: DeckPlan, slideId: string): DeckPlan {
  return updateSlide(plan, slideId, { supports_key_message: true })
}

function editUnits(plan: DeckPlan, slideId: string, edit: (units: EditableUnit[]) => EditableUnit[] | null): DeckPlan {
  return mapSlide(plan, slideId, (slide) => {
    const units = edit([...slide.content_units])
    return units ? { ...slide, content_units: units } : slide
  })
}

export function updateUnit(plan: DeckPlan, slideId: string, position: number, text: string): DeckPlan {
  return editUnits(plan, slideId, (units) => {
    const unit = units[position]
    if (!unit || !isEditableUnit(unit) || unit.text === text) return null
    units[position] = { ...unit, text, locked: true }
    return units
  })
}

export function addUnit(plan: DeckPlan, slideId: string, text = ''): DeckPlan {
  return editUnits(plan, slideId, (units) => [...units, { role: 'bullet', kind: 'bullet', text, locked: true }])
}

export function removeUnit(plan: DeckPlan, slideId: string, position: number): DeckPlan {
  return editUnits(plan, slideId, (units) => {
    const unit = units[position]
    if (!unit || !isEditableUnit(unit)) return null
    units.splice(position, 1)
    return units
  })
}

export function moveUnit(plan: DeckPlan, slideId: string, position: number, delta: -1 | 1): DeckPlan {
  return editUnits(plan, slideId, (units) => {
    const editable = units.map((unit, index) => (isEditableUnit(unit) ? index : -1)).filter((index) => index >= 0)
    const at = editable.indexOf(position)
    const target = editable[at + delta]
    if (at < 0 || target === undefined) return null
    const current = units[position]
    const other = units[target]
    if (!current || !other) return null
    units[position] = other
    units[target] = current
    return units
  })
}

function uniqueId(plan: DeckPlan): string {
  const taken = new Set(plan.slides.map((slide) => slide.id))
  let counter = plan.slides.length + 1
  while (taken.has(`slide-user-${counter}`)) counter += 1
  return `slide-user-${counter}`
}

export function addSlide(plan: DeckPlan, title = 'Новый слайд'): DeckPlan {
  const slide: SlidePlan = {
    id: uniqueId(plan),
    index: plan.slides.length,
    purpose: 'custom',
    title_intent: title,
    key_message: '',
    evidence_ids: [],
    content_units: [],
    density_budget: { level: 'medium' },
    desired_visual: 'none',
    mandatory: false,
  }
  return withSlides(plan, [...ordered(plan), slide])
}

function unitSignature(slide: SlidePlan): unknown[] {
  return slide.content_units.map((unit) => [unit.kind, unit.text ?? null, unit.chart_spec?.chart_type ?? null])
}

function slideSignature(slide: SlidePlan): unknown[] {
  const chart = parseSlideChart(slide.chart)
  return [slide.id, slide.title_intent, slide.key_message, slide.desired_visual ?? null, chart?.type ?? null, slide.supports_key_message ?? null, unitSignature(slide)]
}

function signature(plan: DeckPlan): string {
  return JSON.stringify([plan.brief.key_message ?? null, ordered(plan).map(slideSignature)])
}

export function isPlanEdited(base: DeckPlan, draft: DeckPlan): boolean {
  return signature(base) !== signature(draft)
}

export function editSummary(base: DeckPlan, draft: DeckPlan): { removed: number; added: number; renamed: number; moved: boolean } {
  const before = new Map(base.slides.map((slide) => [slide.id, slide]))
  const after = new Map(draft.slides.map((slide) => [slide.id, slide]))
  const removed = [...before.keys()].filter((id) => !after.has(id)).length
  const added = [...after.keys()].filter((id) => !before.has(id)).length
  const renamed = [...after.values()].filter((slide) => {
    const original = before.get(slide.id)
    return original !== undefined && JSON.stringify(slideSignature(original)) !== JSON.stringify(slideSignature(slide))
  }).length
  const kept = (plan: DeckPlan) =>
    ordered(plan)
      .map((slide) => slide.id)
      .filter((id) => before.has(id) && after.has(id))
      .join('|')
  return { removed, added, renamed, moved: kept(base) !== kept(draft) }
}

function fallbackEvidence(plan: DeckPlan): string | null {
  const refs = plan.slides.flatMap((slide) => slide.evidence_ids)
  const meta = refs.find((ref) => ref.endsWith(':meta'))
  if (meta) return meta
  const pack = refs.map((ref) => ref.split(':')).find((parts) => parts.length > 1)?.[0]
  return pack ? `${pack}:meta` : (refs[0] ?? null)
}

export interface BuildOptions {
  lockAll?: boolean
}

export function prepareForBuild(plan: DeckPlan, { lockAll = false }: BuildOptions = {}): DeckPlan {
  const evidence = fallbackEvidence(plan)
  const slides = ordered(plan).map((slide) => {
    const chartInfo = parseSlideChart(slide.chart)
    const desiredVisual: SlidePlan['desired_visual'] =
      chartInfo?.type === 'table'
        ? 'table'
        : chartInfo?.type && slide.desired_visual === 'table'
          ? 'chart'
          : slide.desired_visual
    const units = slide.content_units
      .filter((unit) => !(isEditableUnit(unit) && (unit.text ?? '').trim() === ''))
      .map((unit) => {
        const titleUnit = unit.kind === 'title' && unit.text !== slide.title_intent
          ? { ...unit, text: slide.title_intent, locked: true }
          : unit
        const lockedUnit = lockAll && isEditableUnit(titleUnit) ? { ...titleUnit, locked: true } : titleUnit
        if (!chartInfo?.type || (lockedUnit.kind !== 'chart' && lockedUnit.kind !== 'table')) return lockedUnit
        if (chartInfo.type === 'table') {
          return lockedUnit.kind === 'table' && lockedUnit.chart_spec === null
            ? lockedUnit
            : { ...lockedUnit, role: 'table', kind: 'table' as const, chart_spec: null }
        }
        const nextChartType = chartInfo.type as NonNullable<ContentUnit['chart_spec']>['chart_type']
        if (lockedUnit.kind === 'chart' && lockedUnit.chart_spec?.chart_type === nextChartType) return lockedUnit
        return {
          ...lockedUnit,
          role: 'chart',
          kind: 'chart' as const,
          chart_spec: {
            ...(lockedUnit.chart_spec ?? { series_count: 1, has_axis_labels: true, has_units: true, has_legend: false }),
            chart_type: nextChartType,
          },
        }
      })
    const missingEvidence = slide.evidence_ids.length === 0 && evidence !== null
    const unitsChanged = units.length !== slide.content_units.length || units.some((unit, index) => unit !== slide.content_units[index])
    const visualChanged = desiredVisual !== slide.desired_visual
    if (!missingEvidence && !unitsChanged && !visualChanged) return slide
    return {
      ...slide,
      desired_visual: desiredVisual,
      content_units: units,
      evidence_ids: missingEvidence ? [evidence] : slide.evidence_ids,
    }
  })
  return withSlides(plan, slides)
}
