import { slidePurposeLabel, type DeckPlan, type SlidePlan } from '@/entities/generation'
import { formatIndex } from '@/shared/lib/format'

export interface EvidenceRef {
  raw: string
  packId: string | null
  sectionId: string | null
  blockId: string | null
  meta: boolean
}

export interface SlideSource {
  key: string
  label: string
  refs: string[]
}

export interface PlanRow {
  id: string
  number: string
  purpose: string
  title: string
  titleIntent: string
  keyMessage: string
  idea: string | null
  visual: string
  sources: SlideSource[]
  sectionId: string | null
  sectionTitle: string | null
}

export interface PlanSectionSummary {
  id: string
  title: string
  count: number
  range: string
}

export interface PlanStats {
  slides: number
  withSources: number
  withVisual: number
  mandatory: number
}

export interface PlanView {
  rows: PlanRow[]
  sections: PlanSectionSummary[]
  stats: PlanStats
}

export const VISUAL_LABEL: Record<string, string> = {
  none: 'Текст',
  image: 'Изображение',
  icon_set: 'Иконки',
  chart: 'График',
  table: 'Таблица',
  diagram: 'Схема',
  screenshot: 'Скриншот',
  quote: 'Цитата',
}

const UNIT_VISUAL: Record<string, string> = {
  chart: 'chart',
  table: 'table',
  diagram: 'diagram',
  image: 'image',
  icon: 'icon_set',
  quote: 'quote',
}

const DECK_PURPOSE_LABEL: Record<string, string> = {
  feature: 'фича',
  product: 'продукт',
  project: 'проект',
  initiative: 'инициатива',
}

const UNASSIGNED_SECTION = 'Вне разделов'

export function deckPurposeLabel(purpose: string): string {
  return DECK_PURPOSE_LABEL[purpose] ?? purpose
}

export function parseEvidenceRef(raw: string): EvidenceRef {
  const [packId = null, second = null, third = null] = raw.split(':')
  if (second === null) return { raw, packId: null, sectionId: null, blockId: null, meta: false }
  if (second === 'meta') return { raw, packId, sectionId: null, blockId: null, meta: true }
  return { raw, packId, sectionId: second, blockId: third, meta: false }
}

export function slideSources(evidenceIds: readonly string[], headings: ReadonlyMap<string, string>): SlideSource[] {
  const sources = new Map<string, SlideSource>()
  for (const raw of evidenceIds) {
    const ref = parseEvidenceRef(raw)
    const key = ref.meta ? 'meta' : (ref.sectionId ?? raw)
    const label = ref.meta ? 'Заголовок контента' : ref.sectionId ? (headings.get(ref.sectionId) ?? ref.sectionId) : raw
    const existing = sources.get(key)
    if (existing) existing.refs.push(raw)
    else sources.set(key, { key, label, refs: [raw] })
  }
  return [...sources.values()]
}

export function plannedVisual(slide: Pick<SlidePlan, 'desired_visual' | 'content_units'>): string | null {
  const unitVisual = slide.content_units.map((unit) => UNIT_VISUAL[unit.kind]).find(Boolean)
  const visual = slide.desired_visual && slide.desired_visual !== 'none' ? slide.desired_visual : (unitVisual ?? slide.desired_visual)
  return visual ? (VISUAL_LABEL[visual] ?? visual) : null
}

function normalize(text: string): string {
  return text.trim().toLowerCase()
}

export function slideIdea(slide: Pick<SlidePlan, 'key_message' | 'title_intent'>, objective: string): string | null {
  const idea = slide.key_message.trim()
  if (!idea) return null
  const repeated = [slide.title_intent, objective].some((text) => normalize(text) === normalize(idea))
  return repeated ? null : idea
}

export function formatSlideRange(numbers: readonly number[]): string {
  const sorted = [...new Set(numbers)].sort((a, b) => a - b)
  if (sorted.length === 0) return 'нет слайдов'
  const runs: [number, number][] = []
  for (const value of sorted) {
    const last = runs.at(-1)
    if (last && value === last[1] + 1) last[1] = value
    else runs.push([value, value])
  }
  const text = runs.map(([start, end]) => (start === end ? String(start) : `${start}–${end}`)).join(', ')
  return `${sorted.length === 1 ? 'слайд' : 'слайды'} ${text}`
}

export function buildPlanView(plan: DeckPlan, headings: ReadonlyMap<string, string>): PlanView {
  const slides = [...plan.slides].sort((a, b) => a.index - b.index)
  const numberById = new Map(slides.map((slide, position) => [slide.id, position + 1]))
  const sections = plan.sections ?? []
  const firstSection = new Map<string, { id: string; title: string }>()
  for (const section of sections) {
    for (const slideId of section.slide_ids) if (!firstSection.has(slideId)) firstSection.set(slideId, section)
  }

  const rows: PlanRow[] = slides.map((slide, position) => {
    const section = firstSection.get(slide.id) ?? null
    return {
      id: slide.id,
      number: formatIndex(position + 1),
      purpose: slidePurposeLabel(slide.purpose) ?? 'Контент',
      title: slide.title_intent.trim() || 'Без заголовка',
      titleIntent: slide.title_intent,
      keyMessage: slide.key_message,
      idea: slideIdea(slide, plan.objective),
      visual: plannedVisual(slide) ?? '',
      sources: slideSources(slide.evidence_ids, headings),
      sectionId: section?.id ?? null,
      sectionTitle: section?.title ?? null,
    }
  })

  const summaries: PlanSectionSummary[] = sections
    .map((section) => {
      const numbers = section.slide_ids.map((id) => numberById.get(id)).filter((value): value is number => value !== undefined)
      return { id: section.id, title: section.title, count: numbers.length, range: formatSlideRange(numbers) }
    })
    .filter((section) => section.count > 0)

  if (summaries.length > 0) {
    const covered = new Set(sections.flatMap((section) => section.slide_ids))
    const outside = slides.filter((slide) => !covered.has(slide.id)).map((slide) => numberById.get(slide.id) ?? 0)
    if (outside.length > 0) summaries.push({ id: 'unassigned', title: UNASSIGNED_SECTION, count: outside.length, range: formatSlideRange(outside) })
  }

  return {
    rows,
    sections: summaries,
    stats: {
      slides: rows.length,
      withSources: rows.filter((row) => row.sources.length > 0).length,
      withVisual: slides.filter((slide) => {
        const visual = plannedVisual(slide)
        return visual !== null && visual !== VISUAL_LABEL.none
      }).length,
      mandatory: slides.filter((slide) => slide.mandatory === true).length,
    },
  }
}
