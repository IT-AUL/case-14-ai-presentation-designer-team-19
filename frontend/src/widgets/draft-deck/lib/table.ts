import type { Insight, Understanding, UnderstandingBlock, UnderstandingDataset } from '@/entities/assistant'
import { pluralize } from '@/shared/lib/format'
import { chartTypeLabel, deduplicateTextBlocks, extractFigure, isInsightRecommendation } from './understanding'

export interface InsightEntry {
  insight: Insight
  dataset: UnderstandingDataset
  preview: UnderstandingBlock['preview']
}

export interface DatasetGroup {
  dataset: UnderstandingDataset
  preview: UnderstandingBlock['preview']
  entries: InsightEntry[]
}

export function normalizeReasonGrammar(text: string): string {
  return text
    .replace(/в задаче сказано про динамика(?!\p{L})/giu, 'в задаче сказано про динамику')
    .replace(/в задаче сказано про структура(?!\p{L})/giu, 'в задаче сказано про структуру')
    .replace(/про динамика(?!\p{L})/giu, 'про динамику')
    .replace(/про структура(?!\p{L})/giu, 'про структуру')
}

const UNCHARTABLE_PREAMBLE = /^эти данные не подходят для графика[^.]*\.\s*/iu

export function datasetChartWhy(dataset: UnderstandingDataset): string | null {
  if (typeof dataset.why === 'string' && dataset.why.trim()) {
    const why = normalizeReasonGrammar(dataset.why.trim())
    return why.replace(UNCHARTABLE_PREAMBLE, '').trim() || why
  }
  const topRec = dataset.recommendations?.[0]
  if (topRec?.reasons && topRec.reasons.length > 0) {
    const cleanReasons = topRec.reasons.map((item) => normalizeReasonGrammar(item.trim())).filter(Boolean)
    if (cleanReasons.length > 0) {
      const label = chartTypeLabel(topRec.chart_type)
      return `${label}: ${cleanReasons.join(', ')}`
    }
  }
  return null
}

export function insightTitle(insight: Insight): string {
  return (insight.headline || insight.fact || '').trim()
}

export function needsDecision(insight: Insight): boolean {
  return isInsightRecommendation(insight) && !insight.confirmed
}

export function datasetGroups(understanding: Understanding): DatasetGroup[] {
  return understanding.datasets.map((dataset) => {
    const preview = understanding.blocks.find((block) => block.dataset_id === dataset.id && block.preview)?.preview ?? null
    return {
      dataset,
      preview,
      entries: dataset.insights.map((insight) => ({ insight, dataset, preview })),
    }
  })
}

export function insightEntries(understanding: Understanding): InsightEntry[] {
  return datasetGroups(understanding).flatMap((group) => group.entries)
}

export function profileFacts(profile: string | null | undefined): string[] {
  if (!profile) return []
  return profile
    .split(';')
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => (part.startsWith('есть пропуски') ? part.replace(/^есть\s+/, '') : part))
}

export function insightSource(insight: Insight, dataset: UnderstandingDataset): string | null {
  const evidence = insight.evidence?.filter((item) => item.trim()) ?? []
  if (evidence.length > 0) return evidence.slice(0, 2).join(' · ')
  return dataset.file ?? null
}

export function compactSource(insight: Insight, dataset: UnderstandingDataset): string | null {
  const known = new Set([dataset.file, dataset.title].filter((item): item is string => Boolean(item)).map((item) => item.trim().toLowerCase()))
  const parts: string[] = []
  for (const row of insight.evidence ?? []) {
    for (const piece of row.split(' · ')) {
      const clean = piece.trim()
      if (!clean || known.has(clean.toLowerCase()) || parts.includes(clean)) continue
      parts.push(clean)
    }
  }
  return parts.length > 0 ? parts.slice(0, 2).join('; ') : null
}

const PROFILE_BASICS = /^(\d[\d\s]*\s+(строк|колон|запис)|период)/iu

export interface ProfileSplit {
  basics: string[]
  details: string[]
}

export function splitProfile(profile: string | null | undefined): ProfileSplit {
  const facts = profileFacts(profile)
  return {
    basics: facts.filter((fact) => PROFILE_BASICS.test(fact)),
    details: facts.filter((fact) => !PROFILE_BASICS.test(fact)),
  }
}

export interface DecisionProgress {
  total: number
  confirmed: number
  pending: number
}

export function decisionProgress(understanding: Understanding, excludedIds: readonly string[] = []): DecisionProgress {
  const excluded = new Set(excludedIds)
  const active = insightEntries(understanding).filter((entry) => !excluded.has(entry.insight.id))
  const confirmed = active.filter((entry) => entry.insight.confirmed).length
  return { total: active.length, confirmed, pending: active.length - confirmed }
}

export function textBlocks(understanding: Understanding): UnderstandingBlock[] {
  return deduplicateTextBlocks(understanding.blocks.filter((block) => !block.dataset_id && (block.title || block.text)))
}

export function blockTitle(block: UnderstandingBlock): string {
  const title = block.title?.trim()
  if (title) return title
  const text = (block.text ?? '').trim()
  const sentence = text.split(/(?<=[.!?])\s/)[0] ?? text
  return sentence.length > 120 ? `${sentence.slice(0, 117)}…` : sentence
}

export interface MaterialCounts {
  tables: number
  textBlocks: number
  numbers: number
}

export function materialCounts(understanding: Understanding): MaterialCounts {
  const insights = insightEntries(understanding)
  const blocks = textBlocks(understanding)
  const numericBlocks = blocks.filter((block) => block.kind === 'number' || /\d/.test(block.text ?? '')).length
  return {
    tables: understanding.datasets.length,
    textBlocks: blocks.length,
    numbers: insights.length + numericBlocks,
  }
}

export function stepNote(understanding: Understanding, excludedInsights = 0): string {
  const insights = insightEntries(understanding)
  const pending = insights.filter((entry) => needsDecision(entry.insight)).length
  const counts = materialCounts(understanding)
  const capacity = understanding.summary.capacity_slides ?? 0
  const parts: string[] = []
  if (counts.tables > 0) parts.push(`${counts.tables} ${pluralize(counts.tables, ['таблица', 'таблицы', 'таблиц'])}`)
  if (counts.textBlocks > 0) parts.push(`${counts.textBlocks} ${pluralize(counts.textBlocks, ['текстовый блок', 'текстовых блока', 'текстовых блоков'])}`)
  if (counts.numbers > 0) parts.push(`${counts.numbers} ${pluralize(counts.numbers, ['числовой показатель', 'числовых показателя', 'числовых показателей'])}`)
  const found = parts.length ? `В материалах: ${parts.join(', ')}.` : 'Материала пока мало.'
  const room = capacity > 0 ? ` Хватит примерно на ${capacity} ${pluralize(capacity, ['слайд', 'слайда', 'слайдов'])}.` : ''
  const skipped = excludedInsights > 0 ? ` ${excludedInsights} ${pluralize(excludedInsights, ['вывод не включу', 'вывода не включу', 'выводов не включу'])}.` : ''
  const ask = pending
    ? ` ${pending} ${pluralize(pending, ['предложение ждёт', 'предложения ждут', 'предложений ждут'])} вашего решения — отметьте выводы на карточках.`
    : ' Отметьте выводы или откройте карточку для правки.'
  return `${found}${room}${skipped}${ask}`
}

export function keyFigure(text: string): string | null {
  return extractFigure(text)
}
