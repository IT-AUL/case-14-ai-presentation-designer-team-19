import type { BlockOps, DataPreview, Insight, InsightPatch, JsonRecord, Understanding, UnderstandingBlock, UnderstandingDataset } from '@/entities/assistant'
import type { ContentPack } from '@/entities/content-pack'
import { pluralize } from '@/shared/lib/format'
import type { Tone } from '@/shared/ui'

export type BlockKindKey = 'topic' | 'fact' | 'number' | 'data' | 'quote' | 'process' | 'other'
export type BlockOriginKind = 'user' | 'data' | 'web' | 'model'

export interface BlockOriginInfo {
  kind: BlockOriginKind
  label: string
  tone: Tone
  detail: string | null
  url: string | null
}

export interface ContextBadgeItem {
  field: string
  label: string
  value: string
  quote: string | null
}

export interface SparkSeriesPoint {
  x: number
  y: number
  value: number
  label: string
}

export interface SparkBarItem {
  x: number
  y: number
  width: number
  height: number
  value: number
  label: string
  isPeak: boolean
}

export interface SparkSegmentItem {
  value: number
  fraction: number
  dashArray: string
  dashOffset: number
  label: string
}

export interface SparkGeometry {
  chartType: 'line' | 'bar' | 'pie' | 'scatter' | 'histogram' | 'kpi' | 'table'
  chartLabel: string
  seriesName: string | null
  unit: string | null
  values: number[]
  categories: string[]
  min: number
  max: number
  firstValue: number | null
  lastValue: number | null
  deltaText: string | null
  linePath: string
  areaPath: string
  trendLinePath: string
  points: SparkSeriesPoint[]
  peakPoint: SparkSeriesPoint | null
  bars: SparkBarItem[]
  segments: SparkSegmentItem[]
}

export const BLOCK_KIND_LABEL: Record<BlockKindKey, string> = {
  topic: 'Тема',
  fact: 'Факт',
  number: 'Цифра',
  data: 'Данные',
  quote: 'Цитата',
  process: 'Процесс',
  other: 'Блок',
}

export const BLOCK_KIND_TONE: Record<BlockKindKey, Tone> = {
  topic: 'ink',
  fact: 'neutral',
  number: 'info',
  data: 'ok',
  quote: 'warn',
  process: 'info',
  other: 'muted',
}

export const CHART_TYPE_LABEL: Record<string, string> = {
  line: 'Линейный график',
  bar: 'Столбчатая диаграмма',
  bar_horizontal: 'Горизонтальные столбцы',
  bar_stacked: 'Накопительные столбцы',
  bar_stacked_100: 'Структура 100%',
  histogram: 'Гистограмма',
  pie: 'Круговая диаграмма',
  doughnut: 'Кольцевая диаграмма',
  donut: 'Кольцевая диаграмма',
  scatter: 'Диаграмма рассеяния',
  kpi: 'Карточки цифр',
  table: 'Таблица',
}

const CONTEXT_FIELD_LABEL: Record<string, string> = {
  audience: 'Аудитория',
  target_slide_count: 'Объём',
  length: 'Объём',
  tone: 'Тон',
  purpose: 'Назначение',
  key_message: 'Главная мысль',
  call_to_action: 'Целевое действие',
}

export function sanitizeAssistantCopy(raw: string): string {
  return raw
    .replace(/из знаний модели/gi, 'общими сведениями')
    .replace(/знания модели/gi, 'общие сведения')
    .replace(/нейросеть/gi, 'сервис')
    .replace(/генерацией/gi, 'сборкой')
    .replace(/генерацию/gi, 'сборку')
    .replace(/генерации/gi, 'сборки')
    .replace(/генерация/gi, 'сборка')
}

export function normalizeBlockKind(kind: string | null | undefined): BlockKindKey {
  if (kind === 'topic' || kind === 'fact' || kind === 'number' || kind === 'data' || kind === 'quote' || kind === 'process') {
    return kind
  }
  return 'other'
}

export function chartTypeLabel(chartType: string | null | undefined): string {
  if (!chartType) return 'График'
  return CHART_TYPE_LABEL[chartType] ?? chartType
}

export function formatCapacitySlides(capacity: number | null | undefined): string {
  if (!capacity || capacity <= 0) return 'Объём определим на следующем шаге'
  return `Материала примерно на ${capacity} ${pluralize(capacity, ['слайд', 'слайда', 'слайдов'])}`
}

export function blockOrigin(block: UnderstandingBlock, datasets: readonly UnderstandingDataset[] = []): BlockOriginInfo {
  const ref = block.source_ref
  if (block.kind === 'data' || block.dataset_id) {
    const dataset = datasets.find((item) => item.id === block.dataset_id || `ds:${item.id}` === block.id)
    const file = dataset?.file ?? (typeof ref?.file === 'string' ? ref.file : null)
    return { kind: 'data', label: 'ваши данные', tone: 'ok', detail: file, url: null }
  }
  const originRaw = typeof ref?.origin === 'string' ? ref.origin : null
  const url = typeof ref?.url === 'string' ? ref.url : null
  if (originRaw === 'web' || url) {
    const site = typeof ref?.site === 'string' ? ref.site : typeof ref?.publisher === 'string' ? ref.publisher : 'интернет'
    return { kind: 'web', label: site, tone: 'info', detail: typeof ref?.title === 'string' ? ref.title : url, url }
  }
  if (originRaw === 'model') {
    return { kind: 'model', label: 'общие сведения', tone: 'warn', detail: 'Дополнено без внешнего источника — проверьте формулировку', url: null }
  }
  const artifactId = typeof ref?.artifact_id === 'string' ? ref.artifact_id : null
  if (artifactId === 'user:notes') {
    return { kind: 'user', label: 'ваш тезис', tone: 'neutral', detail: 'Добавлено вами на шаге разбора', url: null }
  }
  const headingPath = Array.isArray(ref?.heading_path) ? ref.heading_path.filter((item): item is string => typeof item === 'string').join(' / ') : null
  return { kind: 'user', label: 'ваш текст', tone: 'neutral', detail: headingPath ?? artifactId, url: null }
}

export function formatSourceDetails(block: UnderstandingBlock, datasets: readonly UnderstandingDataset[] = []): string[] {
  const lines: string[] = []
  const ref = block.source_ref
  const dataset = datasets.find((item) => item.id === block.dataset_id || `ds:${item.id}` === block.id)
  if (dataset?.file) lines.push(`Файл: ${dataset.file}`)
  if (dataset?.profile_summary) lines.push(`Профиль: ${dataset.profile_summary}`)
  if (ref) {
    if (typeof ref.file === 'string' && ref.file !== dataset?.file) lines.push(`Файл: ${ref.file}`)
    if (typeof ref.sheet === 'string') lines.push(`Лист: ${ref.sheet}`)
    if (typeof ref.page === 'number' || typeof ref.page === 'string') lines.push(`Слайд / стр.: ${String(ref.page)}`)
    if (typeof ref.range === 'string') lines.push(`Диапазон: ${ref.range}`)
    if (Array.isArray(ref.heading_path) && ref.heading_path.length > 0) {
      const path = ref.heading_path.filter((item): item is string => typeof item === 'string').join(' → ')
      if (path) lines.push(`Раздел: ${path}`)
    }
    if (typeof ref.quote === 'string' && ref.quote.trim()) lines.push(`Цитата: «${ref.quote.trim()}»`)
    if (typeof ref.artifact_id === 'string') {
      lines.push(ref.artifact_id === 'user:notes' ? 'Источник: тезисы автора' : `Артефакт: ${ref.artifact_id}`)
    }
  }
  if (block.preview && block.preview.categories.length > 0) {
    const first = block.preview.categories[0]
    const last = block.preview.categories.at(-1)
    lines.push(`Точек данных: ${block.preview.categories.length}${first && last ? ` (${first} — ${last})` : ''}`)
  }
  if (lines.length === 0 && block.text) {
    lines.push(`Исходный фрагмент: ${block.text}`)
  }
  return lines
}

export function extractContextBadges(context: JsonRecord | null | undefined): ContextBadgeItem[] {
  if (!context || !Array.isArray(context.fields)) return []
  const result: ContextBadgeItem[] = []
  for (const item of context.fields) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) continue
    const record = item as JsonRecord
    const field = typeof record.field === 'string' ? record.field : ''
    const rawValue = record.value
    const value = typeof rawValue === 'string' ? rawValue.trim() : typeof rawValue === 'number' ? String(rawValue) : ''
    if (!field || !value) continue
    const label = CONTEXT_FIELD_LABEL[field] ?? field
    const quote = typeof record.quote === 'string' && record.quote.trim() ? record.quote.trim() : null
    result.push({ field, label, value, quote })
  }
  return result
}

export function isInsightDefaultIncluded(insight: Pick<Insight, 'confirmed' | 'needs_confirmation' | 'implication' | 'kind' | 'strength'>): boolean {
  if (insight.confirmed) return true
  if (insight.needs_confirmation || insight.kind === 'recommendation' || Boolean(insight.implication)) return false
  return (insight.strength ?? 0) >= 0.7
}

export function isInsightRecommendation(insight: Pick<Insight, 'needs_confirmation' | 'implication' | 'kind'>): boolean {
  return Boolean(insight.needs_confirmation || insight.kind === 'recommendation' || insight.implication)
}

function normalizeTextKey(value: string | null | undefined): string {
  return (value ?? '').trim().replace(/\s+/g, ' ').toLowerCase()
}

export function extractFigure(text: string): string | null {
  const times = /×\s*(\d+(?:[.,]\d+)?)|в\s+(\d+(?:[.,]\d+)?)\s+раз/i.exec(text)
  if (times) return `×${times[1] ?? times[2]}`
  const share = /(\d+(?:[.,]\d+)?)\s?%/.exec(text)
  if (share) return `${share[1]}%`
  const money = /(\d+(?:[.,]\d+)?)\s*(млн\.?|млрд\.?|тыс\.?)(?:\s*(₽|\$|€|руб\.?))?/i.exec(text)
  if (money) {
    const amount = money[1]
    const unit = money[2]
    const currency = money[3]
    return currency ? `${amount} ${unit} ${currency}` : `${amount} ${unit}`
  }
  const directCurrency = /([+-]?\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?)\s*(₽|\$|€)/.exec(text)
  if (directCurrency) return `${directCurrency[1]} ${directCurrency[2]}`
  return null
}

const SUMMARY_PREFIX_LEN = 60
const SUMMARY_MIN_OVERLAP = 16

function summaryOverlap(a: string, b: string): boolean {
  if (!a || !b) return false
  const [shorter, longer] = a.length <= b.length ? [a, b] : [b, a]
  if (shorter.length < SUMMARY_MIN_OVERLAP) return longer.startsWith(shorter)
  return longer.includes(shorter) || longer.startsWith(shorter.slice(0, SUMMARY_PREFIX_LEN))
}

export function deduplicateTextBlocks<
  T extends {
    title?: string | null
    text?: string | null
    heading?: string | null
    summary?: string | null
    dataset_id?: string | null
    kind?: string | null
  },
>(blocks: readonly T[]): T[] {
  const seen = new Map<string, number>()
  const byHeading = new Map<string, number[]>()
  const result: T[] = []
  for (const block of blocks) {
    if (block.dataset_id) {
      result.push(block)
      continue
    }
    const heading = normalizeTextKey(block.heading ?? block.title)
    const summary = normalizeTextKey(block.summary ?? block.text)
    if (!heading && !summary) continue
    const key = `${heading}\u0000${summary}`
    const existingIndex = seen.get(key)
    if (existingIndex !== undefined) {
      const existing = result[existingIndex]
      if (existing && existing.kind === 'topic' && block.kind && block.kind !== 'topic') {
        result[existingIndex] = block
      }
      continue
    }
    if (heading && block.kind !== 'topic') {
      const dupIndex = (byHeading.get(heading) ?? []).find((index) => {
        const other = result[index]
        return other ? other.kind !== 'topic' && summaryOverlap(summary, normalizeTextKey(other.summary ?? other.text)) : false
      })
      if (dupIndex !== undefined) {
        const existing = result[dupIndex]
        const existingSummary = normalizeTextKey(existing?.summary ?? existing?.text)
        if (summary.length > existingSummary.length) {
          result[dupIndex] = block
        }
        seen.set(key, dupIndex)
        continue
      }
    }
    seen.set(key, result.length)
    if (heading) byHeading.set(heading, [...(byHeading.get(heading) ?? []), result.length])
    result.push(block)
  }
  return result
}

export function applyOptimisticBlockOps(understanding: Understanding, ops: BlockOps['ops']): Understanding {
  let blocks = understanding.blocks.map((block) => ({ ...block }))
  for (const op of ops) {
    const index = blocks.findIndex((block) => block.id === op.block_id)
    if (index < 0) continue
    if (op.op === 'remove') {
      const removed = blocks[index]
      const removedKey =
        removed && !removed.dataset_id
          ? `${normalizeTextKey(removed.title)}\u0000${normalizeTextKey(removed.text)}`
          : null
      blocks = blocks.filter((block) => {
        if (block.id === op.block_id) return false
        if (removedKey && !block.dataset_id) {
          const key = `${normalizeTextKey(block.title)}\u0000${normalizeTextKey(block.text)}`
          if (key === removedKey) return false
        }
        return true
      })
    } else if (op.op === 'edit') {
      const current = blocks[index]
      if (!current) continue
      const title = typeof op.title === 'string' ? op.title : current.title
      const text = typeof op.text === 'string' ? op.text : Array.isArray(op.items) ? op.items.map(String).join('\n') : current.text
      blocks[index] = { ...current, title, text }
    } else if (op.op === 'move') {
      const targetIndex = typeof op.index === 'number' ? Math.max(0, Math.min(op.index, blocks.length - 1)) : index
      const [moved] = blocks.splice(index, 1)
      if (moved) blocks.splice(targetIndex, 0, moved)
    } else if (op.op === 'merge') {
      const intoId = typeof op.into === 'string' ? op.into : ''
      const source = blocks[index]
      const targetIndex = blocks.findIndex((block) => block.id === intoId)
      const target = blocks[targetIndex]
      if (source && target && targetIndex >= 0 && targetIndex !== index) {
        const mergedText = [target.text, source.text].filter(Boolean).join('\n')
        blocks[targetIndex] = { ...target, text: mergedText }
        blocks.splice(index, 1)
      }
    }
  }
  return { ...understanding, blocks }
}

export function applyOptimisticInsightPatch(understanding: Understanding, insightId: string, patch: InsightPatch): Understanding {
  const datasets = understanding.datasets.map((dataset) => ({
    ...dataset,
    insights: dataset.insights.map((insight) => {
      if (insight.id !== insightId) return insight
      const nextConfirmed = patch.confirmed !== undefined ? Boolean(patch.confirmed) : typeof patch.text === 'string' && patch.text.trim() ? true : insight.confirmed
      const nextHeadline = typeof patch.text === 'string' && patch.text.trim() ? patch.text.trim() : insight.headline
      const nextMeaning = patch.meaning !== undefined ? patch.meaning : insight.meaning
      const nextImplication = patch.implication !== undefined ? patch.implication : insight.implication
      const nextNeedsConfirmation =
        patch.confirmed !== undefined && (insight.implication || nextImplication) ? !patch.confirmed : insight.needs_confirmation
      return {
        ...insight,
        confirmed: nextConfirmed,
        headline: nextHeadline,
        meaning: nextMeaning,
        implication: nextImplication,
        needs_confirmation: nextNeedsConfirmation,
      }
    }),
  }))
  return { ...understanding, datasets }
}

export function applyOptimisticNotes(understanding: Understanding, items: readonly string[]): Understanding {
  const clean = items.map((item) => item.trim()).filter(Boolean)
  if (clean.length === 0) return understanding
  const stamp = understanding.blocks.length + 1
  const sectionId = `sec-notes-${stamp}`
  const topicBlock: UnderstandingBlock = {
    id: `s:${sectionId}`,
    kind: 'topic',
    title: 'Тезисы автора',
    text: clean.slice(0, 2).join(' '),
    source_ref: { artifact_id: 'user:notes' },
    dataset_id: null,
    preview: null,
  }
  const itemBlocks: UnderstandingBlock[] = clean.map((text, index) => ({
    id: `b:${sectionId}:${index}`,
    kind: 'fact',
    title: 'Тезисы автора',
    text,
    source_ref: { artifact_id: 'user:notes' },
    dataset_id: null,
    preview: null,
  }))
  return {
    ...understanding,
    blocks: [...understanding.blocks, topicBlock, ...itemBlocks],
    summary: {
      ...understanding.summary,
      main_points: [...new Set([...understanding.summary.main_points, 'Тезисы автора'])].slice(0, 4),
    },
  }
}

const STALE_PARTS_NOTE_RE = /материалы\s+в\s+\d+\s+част/i

function formatMaterialSummaryParts(tables: number, textBlockCount: number, numbers: number): string {
  const parts: string[] = []
  if (tables > 0) parts.push(`${tables} ${pluralize(tables, ['таблица', 'таблицы', 'таблиц'])}`)
  if (textBlockCount > 0) parts.push(`${textBlockCount} ${pluralize(textBlockCount, ['текстовый блок', 'текстовых блока', 'текстовых блоков'])}`)
  if (numbers > 0) parts.push(`${numbers} ${pluralize(numbers, ['числовой показатель', 'числовых показателя', 'числовых показателей'])}`)
  return parts.join(', ')
}

export function resolveUnderstandingNote(
  understanding: Understanding | null | undefined,
  sessionNote: string | null | undefined,
  feedbackNote: string | null,
): string {
  if (feedbackNote) return sanitizeAssistantCopy(feedbackNote)
  if (understanding?.interpretation?.status === 'pending') {
    return 'Факты и графики по таблицам уже посчитаны, дописываю краткие пояснения к цифрам…'
  }
  const apiNote = understanding?.assistant_note ?? understanding?.summary.assistant_note
  if (apiNote && apiNote.trim() && !STALE_PARTS_NOTE_RE.test(apiNote)) return sanitizeAssistantCopy(apiNote.trim())
  if (sessionNote && sessionNote.trim() && !STALE_PARTS_NOTE_RE.test(sessionNote)) return sanitizeAssistantCopy(sessionNote.trim())
  if (!understanding) return 'Разбираю структуру ваших материалов и считаю показатели по таблицам…'
  if ((understanding.summary.capacity_slides ?? 0) <= 2 && understanding.datasets.length === 0) {
    return 'Материала пока на пару слайдов — на следующем шаге решим, сделать короткую колоду или добрать факты.'
  }
  const tables = understanding.datasets.length
  const nonDataBlocks = deduplicateTextBlocks(understanding.blocks.filter((block) => !block.dataset_id && (block.title || block.text)))
  const insightCount = understanding.datasets.reduce((sum, ds) => sum + ds.insights.length, 0)
  const numericBlocks = nonDataBlocks.filter((block) => block.kind === 'number' || /\d/.test(block.text ?? '')).length
  const summaryParts = formatMaterialSummaryParts(tables, nonDataBlocks.length, insightCount + numericBlocks)
  if (understanding.datasets.length > 0) {
    return summaryParts
      ? `В материалах: ${summaryParts}. В таблицах нашёл готовые сюжеты с цифрами — проверьте выводы и отметьте предложения, с которыми согласны.`
      : 'В таблицах нашёл готовые сюжеты с цифрами — проверьте выводы и отметьте предложения, с которыми согласны.'
  }
  return summaryParts
    ? `В материалах: ${summaryParts}. Проверьте, как я понял ваши материалы: лишние блоки можно убрать, а важные выводы — уточнить.`
    : 'Проверьте, как я понял ваши материалы: лишние блоки можно убрать, а важные выводы — уточнить.'
}

function normalizeChartFamily(chartType: string | null | undefined): SparkGeometry['chartType'] {
  if (!chartType) return 'line'
  if (chartType === 'pie' || chartType === 'doughnut' || chartType === 'donut') return 'pie'
  if (chartType === 'scatter') return 'scatter'
  if (chartType === 'histogram') return 'histogram'
  if (chartType === 'kpi') return 'kpi'
  if (chartType === 'table') return 'table'
  if (chartType.startsWith('bar') || chartType === 'column') return 'bar'
  return 'line'
}

function buildTrendLinePath(points: readonly SparkSeriesPoint[], padX: number, plotW: number, padTop: number, plotH: number): string {
  if (points.length < 2) return ''
  const n = points.length
  const meanX = points.reduce((sum, pt) => sum + pt.x, 0) / n
  const meanY = points.reduce((sum, pt) => sum + pt.y, 0) / n
  let num = 0
  let den = 0
  for (const pt of points) {
    const dx = pt.x - meanX
    num += dx * (pt.y - meanY)
    den += dx * dx
  }
  if (den <= 0) return ''
  const slope = num / den
  const intercept = meanY - slope * meanX
  const x1 = padX
  const x2 = padX + plotW
  const clampY = (y: number) => Number(Math.max(padTop, Math.min(padTop + plotH, y)).toFixed(1))
  return `M${Number(x1.toFixed(1))},${clampY(intercept + slope * x1)} L${Number(x2.toFixed(1))},${clampY(intercept + slope * x2)}`
}

export function buildSparkGeometry(preview: DataPreview | null | undefined, fallbackChartType?: string | null, width = 220, height = 64): SparkGeometry {
  const rawType = fallbackChartType ?? preview?.chart_type ?? 'line'
  const chartType = normalizeChartFamily(rawType)
  const chartLabel = chartTypeLabel(rawType)
  const firstSeries = preview?.series?.[0]
  const rawValues = firstSeries?.values ?? []
  const values = rawValues.filter((val): val is number => typeof val === 'number' && Number.isFinite(val))
  const categories = preview?.categories ?? []
  const seriesName = firstSeries?.name ?? null
  const unit = preview?.unit ?? null

  if (values.length === 0) {
    return {
      chartType,
      chartLabel,
      seriesName,
      unit,
      values: [],
      categories,
      min: 0,
      max: 0,
      firstValue: null,
      lastValue: null,
      deltaText: null,
      linePath: '',
      areaPath: '',
      trendLinePath: '',
      points: [],
      peakPoint: null,
      bars: [],
      segments: [],
    }
  }

  const min = Math.min(...values)
  const max = Math.max(...values)
  const firstValue = values[0] ?? null
  const lastValue = values.at(-1) ?? null
  let deltaText: string | null = null
  if (firstValue !== null && lastValue !== null && firstValue > 0 && values.length > 1) {
    const ratio = lastValue / firstValue
    if (ratio >= 1.8) {
      deltaText = `×${ratio.toFixed(1).replace('.', ',')}`
    } else {
      const pct = Math.round(((lastValue - firstValue) / firstValue) * 100)
      deltaText = `${pct >= 0 ? '+' : ''}${pct}%`
    }
  }

  const padX = 8
  const padTop = 8
  const padBottom = 8
  const plotW = Math.max(16, width - padX * 2)
  const plotH = Math.max(16, height - padTop - padBottom)
  const span = max - min || 1

  const parsedX =
    chartType === 'scatter' && categories.length === values.length
      ? categories.map((cat) => Number(String(cat).replace(',', '.').replace(/[^0-9.-]/g, '')))
      : []
  const hasNumericX = parsedX.length === values.length && parsedX.every((val) => Number.isFinite(val))
  const minX = hasNumericX ? Math.min(...parsedX) : 0
  const maxX = hasNumericX ? Math.max(...parsedX) : 0
  const spanX = maxX - minX

  const points: SparkSeriesPoint[] = values.map((value, idx) => {
    const xRatio =
      values.length === 1
        ? 0.5
        : hasNumericX && spanX > 0
          ? ((parsedX[idx] ?? minX) - minX) / spanX
          : idx / (values.length - 1)
    const x = Number((padX + xRatio * plotW).toFixed(1))
    const y = Number((padTop + plotH - ((value - min) / span) * plotH).toFixed(1))
    return { x, y, value, label: categories[idx] ?? String(idx + 1) }
  })

  let peakPoint: SparkSeriesPoint | null = null
  for (const pt of points) {
    if (!peakPoint || pt.value > peakPoint.value) peakPoint = pt
  }

  const linePath = points.map((pt, idx) => `${idx === 0 ? 'M' : 'L'}${pt.x},${pt.y}`).join(' ')
  const firstPt = points[0]
  const lastPt = points.at(-1)
  const baselineY = padTop + plotH
  const areaPath =
    firstPt && lastPt ? `${linePath} L${lastPt.x},${baselineY} L${firstPt.x},${baselineY} Z` : ''
  const trendLinePath = buildTrendLinePath(points, padX, plotW, padTop, plotH)

  const barCount = Math.min(values.length, 24)
  const stepW = plotW / barCount
  const barRatio = chartType === 'histogram' ? 0.88 : 0.68
  const barW = Math.max(3, Number((stepW * barRatio).toFixed(1)))
  const baseMin = Math.min(0, min)
  const barSpan = max - baseMin || 1
  const bars: SparkBarItem[] = values.slice(0, barCount).map((value, idx) => {
    const h = Math.max(3, Number((((value - baseMin) / barSpan) * plotH).toFixed(1)))
    const x = Number((padX + idx * stepW + (stepW - barW) / 2).toFixed(1))
    const y = Number((padTop + plotH - h).toFixed(1))
    return { x, y, width: barW, height: h, value, label: categories[idx] ?? String(idx + 1), isPeak: value === max }
  })

  const total = values.reduce((acc, v) => acc + Math.max(0, v), 0) || 1
  const circumference = 2 * Math.PI * 18
  let offset = 0
  const segments: SparkSegmentItem[] = values.slice(0, 5).map((value, idx) => {
    const fraction = Math.max(0, value) / total
    const len = Number((fraction * circumference).toFixed(2))
    const item: SparkSegmentItem = {
      value,
      fraction,
      dashArray: `${len} ${Number((circumference - len).toFixed(2))}`,
      dashOffset: Number((-offset).toFixed(2)),
      label: categories[idx] ?? `Доля ${idx + 1}`,
    }
    offset += len
    return item
  })

  return {
    chartType,
    chartLabel,
    seriesName,
    unit,
    values,
    categories,
    min,
    max,
    firstValue,
    lastValue,
    deltaText,
    linePath,
    areaPath,
    trendLinePath,
    points,
    peakPoint,
    bars,
    segments,
  }
}

export function fallbackUnderstandingFromContentPack(pack: ContentPack): Understanding {
  const rawBlocks: UnderstandingBlock[] = []
  for (const section of pack.sections) {
    const firstBlock = section.blocks[0]
    const previewLines = section.blocks
      .flatMap((b) => (b.items && b.items.length > 0 ? b.items : b.text ? [b.text] : []))
      .slice(0, 2)
      .join(' ')
    if (section.heading) {
      rawBlocks.push({
        id: `s:${section.id}`,
        kind: 'topic',
        title: section.heading,
        text: previewLines || null,
        source_ref: (firstBlock?.source_ref as JsonRecord | undefined) ?? { artifact_id: pack.id },
        dataset_id: null,
        preview: null,
      })
    }
    section.blocks.forEach((block, index) => {
      const text = block.text ?? (block.items ? block.items.join('\n') : '')
      if (!text.trim()) return
      const hasNumber = /\d/.test(text)
      rawBlocks.push({
        id: `b:${section.id}:${index}`,
        kind: block.kind === 'quote' ? 'quote' : hasNumber ? 'number' : 'fact',
        title: section.heading || null,
        text,
        source_ref: (block.source_ref as JsonRecord | undefined) ?? { artifact_id: pack.id },
        dataset_id: null,
        preview: null,
      })
    })
  }
  const blocks = deduplicateTextBlocks(rawBlocks)
  const datasets: UnderstandingDataset[] = (pack.datasets ?? []).map((ds) => ({
    id: ds.id,
    title: ds.title,
    file: ds.file_name ?? null,
    profile_summary:
      [
        ds.row_count > 0 ? `${ds.row_count} ${pluralize(ds.row_count, ['строка', 'строки', 'строк'])}` : null,
        ds.columns?.length ? `${ds.columns.length} ${pluralize(ds.columns.length, ['колонка', 'колонки', 'колонок'])}` : null,
      ]
        .filter(Boolean)
        .join('; ') || null,
    task: null,
    task_ru: null,
    recommendations: [],
    why: null,
    insights: [],
  }))
  const mainPoints = pack.sections
    .map((s) => s.heading)
    .filter((h): h is string => Boolean(h && h.trim()))
    .slice(0, 3)
  const capacity = Math.max(3, Math.min(12, pack.sections.length + datasets.length + 2))
  const numberCount = blocks.filter((b) => b.kind === 'number').length + datasets.length
  const summaryParts = formatMaterialSummaryParts(datasets.length, blocks.length, numberCount)
  const note = summaryParts ? `В материалах: ${summaryParts}.` : `Разобрал материалы: разделов — ${pack.sections.length}.`
  return {
    content_pack_id: pack.id,
    blocks,
    summary: {
      capacity_slides: capacity,
      main_points: mainPoints,
      assistant_note: note,
    },
    datasets,
    brief: {},
    context: null,
    interpretation: null,
    assistant_note: note,
  }
}
