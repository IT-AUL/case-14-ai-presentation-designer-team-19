import { hasChartData, isEditableUnit, slidePurposeLabel, unitOrigin, type DeckPlan, type SlidePlan, type UnitOrigin } from '@/entities/generation'
import { formatIndex, pluralize } from '@/shared/lib/format'

export type QuickActionKind = 'shorten' | 'split' | 'merge_next' | 'to_chart' | 'to_diagram' | 'remove'

export const EDITOR_PURPOSES = ['title', 'summary', 'data', 'comparison', 'roadmap', 'closing'] as const
export type EditorSlidePurpose = (typeof EDITOR_PURPOSES)[number]

export const EDITOR_PURPOSE_LABEL: Record<EditorSlidePurpose, string> = {
  title: 'Титул',
  summary: 'Вывод',
  data: 'Данные',
  comparison: 'Сравнение',
  roadmap: 'План',
  closing: 'Финал',
}

export interface SufficiencyLike {
  status?: string | null
  contentSlides?: number | null
  targetSlides?: number | null
  estimated_slides?: number | null
  content_slides?: number | null
  target_slides?: number | null
}

export interface SufficiencyBannerView {
  kind: 'short' | 'enough' | 'excess'
  estimatedSlides: number | null
  targetSlides: number
  text: string
  canExpand: boolean
}

export interface SlideChartOption {
  type: string
  label: string
  active: boolean
  reasons: string[]
}

export interface SlideChartView {
  type: string
  label: string
  why: string
  reasons: string[]
  options: SlideChartOption[]
  viewId: string | null
  previewArtifactId: string | null
}

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
  chart: SlideChartView | null
  supportsKeyMessage: boolean | null
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
  icon: 'Иконки',
}

export const CHART_TYPE_LABEL: Record<string, string> = {
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

export function chartTypeLabel(type: string): string {
  return CHART_TYPE_LABEL[type] ?? type
}

export const EDITOR_VISUALS = ['none', 'chart', 'table', 'diagram', 'image'] as const satisfies readonly NonNullable<SlidePlan['desired_visual']>[]

export const ORIGIN_LABEL: Record<UnitOrigin, string> = {
  user: 'ваш контент',
  web: 'интернет',
  model: 'знания модели',
}

export type SourceKind = 'table' | 'text' | 'web'

export const SOURCE_KIND_LABEL: Record<SourceKind, string> = {
  table: 'Из таблицы',
  text: 'Из текста',
  web: 'Из интернета',
}

const SOURCE_KIND_ORDER: readonly SourceKind[] = ['table', 'text', 'web']

const BOOKENDS = new Set(['title', 'thank_you', 'cta', 'agenda', 'closing'])

const TECHNICAL_KEY_MESSAGES = new Set([
  'presentation',
  'презентация',
  'feature',
  'product',
  'project',
  'initiative',
])

export function isMeaningfulKeyMessage(value: unknown): value is string {
  if (typeof value !== 'string') return false
  const trimmed = value.trim()
  if (!trimmed) return false
  return !TECHNICAL_KEY_MESSAGES.has(trimmed.toLowerCase())
}

export function resolveEditorPurpose(purpose: string): EditorSlidePurpose | null {
  if (purpose === 'title') return 'title'
  if (purpose === 'summary' || purpose === 'overview') return 'summary'
  if (purpose === 'data') return 'data'
  if (purpose === 'comparison') return 'comparison'
  if (purpose === 'roadmap' || purpose === 'agenda' || purpose === 'timeline' || purpose === 'process') return 'roadmap'
  if (purpose === 'closing' || purpose === 'thank_you' || purpose === 'cta') return 'closing'
  return null
}

export interface RoleSlideLike {
  id: string
  purpose: string
  evidence_ids: readonly string[]
  content_units?: readonly { origin?: string | null }[]
}

export function isUserAddedSlide(slide: RoleSlideLike): boolean {
  if (slide.purpose !== 'custom') return false
  if (slide.id.startsWith('slide-user-')) return true
  if (slide.evidence_ids.some((raw) => raw.trim().length > 0)) return false
  const units = slide.content_units ?? []
  if (units.some((unit) => unit.origin === 'user')) return true
  return !/^slide-\d+$/i.test(slide.id)
}

export function slideRoleLabel(slide: RoleSlideLike): string {
  if (slide.purpose === 'custom') {
    return isUserAddedSlide(slide) ? 'Свой слайд' : 'Контент'
  }
  if (slide.purpose in EDITOR_PURPOSE_LABEL) {
    return EDITOR_PURPOSE_LABEL[slide.purpose as EditorSlidePurpose]
  }
  return slidePurposeLabel(slide.purpose) ?? 'Контент'
}

export function resolveSufficiencyBanner(
  sufficiency: SufficiencyLike | null | undefined,
  fallbackTargetSlides?: number | null,
): SufficiencyBannerView | null {
  if (!sufficiency) return null
  const estimatedRaw = sufficiency.contentSlides ?? sufficiency.estimated_slides ?? sufficiency.content_slides ?? null
  const estimated = typeof estimatedRaw === 'number' && Number.isFinite(estimatedRaw) ? estimatedRaw : null
  const targetRaw = fallbackTargetSlides ?? sufficiency.targetSlides ?? sufficiency.target_slides ?? null
  const target = typeof targetRaw === 'number' && Number.isFinite(targetRaw) && targetRaw > 0 ? targetRaw : 8
  const status = sufficiency.status ?? null

  if (estimated !== null && estimated < target) {
    return {
      kind: 'short',
      estimatedSlides: estimated,
      targetSlides: target,
      text: `Материала хватает на ${estimated} ${pluralize(estimated, ['слайд', 'слайда', 'слайдов'])} из ${target}.`,
      canExpand: true,
    }
  }

  if (estimated !== null && estimated >= target) {
    const reserve = estimated > target ? `, запас — до ${estimated}` : ''
    const baseText = `Материала достаточно для ${target} ${pluralize(target, ['слайда', 'слайдов', 'слайдов'])}${reserve}`
    if (status === 'excess') {
      return {
        kind: 'excess',
        estimatedSlides: estimated,
        targetSlides: target,
        text: `${baseText}. Самое важное оставлю, остальное сожму.`,
        canExpand: false,
      }
    }
    return {
      kind: 'enough',
      estimatedSlides: estimated,
      targetSlides: target,
      text: `${baseText}.`,
      canExpand: false,
    }
  }

  if (status === 'excess') {
    return {
      kind: 'excess',
      estimatedSlides: null,
      targetSlides: target,
      text: `Материала больше, чем помещается в ${target} ${pluralize(target, ['слайд', 'слайда', 'слайдов'])}. Самое важное оставлю, остальное сожму.`,
      canExpand: false,
    }
  }

  return null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0) : []
}

export function normalizeChartReasonText(text: string): string {
  return text
    .replace(/в задаче сказано про динамика(?!\p{L})/giu, 'в задаче сказано про динамику')
    .replace(/в задаче сказано про структура(?!\p{L})/giu, 'в задаче сказано про структуру')
    .replace(/про динамика(?!\p{L})/giu, 'про динамику')
    .replace(/про структура(?!\p{L})/giu, 'про структуру')
}

export const DEFAULT_CHART_REASONS: Record<string, string[]> = {
  line: ['временной ряд', 'показывает динамику и темп изменений'],
  bar: ['прямое сравнение величин между категориями'],
  bar_horizontal: ['удобно сравнивать категории с длинными подписями'],
  bar_stacked: ['показывает общий объём и вклад частей по группам'],
  bar_stacked_100: ['сравнивает доли категорий в 100% шкале'],
  pie: ['наглядно показывает доли в общем объёме'],
  doughnut: ['показывает структуру долей с акцентом на итог'],
  donut: ['показывает структуру долей с акцентом на итог'],
  scatter: ['показывает связь и разброс двух числовых показателей'],
  histogram: ['показывает распределение значений по интервалам'],
  kpi: ['выделяет ключевые цифры крупным планом'],
  table: ['точные значения по всем строкам и столбцам'],
}

export const DEFAULT_CHART_ALTERNATIVES: Record<string, { type: string; reasons: string[] }[]> = {
  line: [
    { type: 'bar', reasons: ['сравнение отдельных периодов по высоте столбцов'] },
    { type: 'table', reasons: ['точные цифры по каждому периоду без округления'] },
  ],
  bar: [
    { type: 'bar_horizontal', reasons: ['длинные названия категорий читаются без наклона'] },
    { type: 'table', reasons: ['точные значения и сравнение нескольких метрик'] },
  ],
  bar_horizontal: [
    { type: 'bar', reasons: ['компактное сравнение категорий по вертикали'] },
    { type: 'table', reasons: ['точные цифры по каждой позиции'] },
  ],
  bar_stacked: [
    { type: 'bar', reasons: ['прямое сравнение итоговых значений по группам'] },
    { type: 'bar_stacked_100', reasons: ['акцент на долях вместо абсолютных сумм'] },
  ],
  bar_stacked_100: [
    { type: 'bar_stacked', reasons: ['сохраняет абсолютный масштаб по группам'] },
    { type: 'table', reasons: ['точные проценты и абсолютные числа рядом'] },
  ],
  pie: [
    { type: 'bar_horizontal', reasons: ['точнее сравнивает близкие по размеру доли'] },
    { type: 'table', reasons: ['точные доли и абсолютные значения'] },
  ],
  doughnut: [
    { type: 'bar_horizontal', reasons: ['точнее сравнивает близкие по размеру доли'] },
    { type: 'table', reasons: ['точные доли и абсолютные значения'] },
  ],
  donut: [
    { type: 'bar_horizontal', reasons: ['точнее сравнивает близкие по размеру доли'] },
    { type: 'table', reasons: ['точные доли и абсолютные значения'] },
  ],
  scatter: [
    { type: 'bar', reasons: ['сравнение объектов по главной метрике'] },
    { type: 'table', reasons: ['точные пары координат по каждому объекту'] },
  ],
  histogram: [
    { type: 'bar', reasons: ['сравнение отдельных групп значений'] },
    { type: 'table', reasons: ['точные границы интервалов и частоты'] },
  ],
  kpi: [
    { type: 'bar', reasons: ['наглядное сравнение показателей по масштабу'] },
    { type: 'table', reasons: ['сводка всех показателей в одной сетке'] },
  ],
  table: [
    { type: 'bar', reasons: ['быстрое визуальное сравнение лидеров'] },
    { type: 'line', reasons: ['наглядная динамика по периодам'] },
  ],
}

export type ChartSlideLike = Pick<SlidePlan, 'chart'> & {
  purpose?: string | null
  title_intent?: string | null
  key_message?: string | null
  content_units?: readonly {
    kind?: string
    text?: string | null
    chart_ref?: string | null
    chart_spec?: unknown
  }[] | null
}

function inferChartRecordFromSlide(slide: ChartSlideLike): Record<string, unknown> | null {
  const units = Array.isArray(slide.content_units) ? slide.content_units : []
  const chartUnit = units.find(
    (unit) => isRecord(unit.chart_spec) || unit.kind === 'chart' || (typeof unit.chart_ref === 'string' && unit.chart_ref.trim().length > 0),
  )
  if (chartUnit) {
    const spec = isRecord(chartUnit.chart_spec) ? chartUnit.chart_spec : null
    const rawType =
      (spec && typeof spec.chart_type === 'string' && spec.chart_type.trim() ? spec.chart_type.trim() : null) ??
      (spec && typeof spec.type === 'string' && spec.type.trim() ? spec.type.trim() : null) ??
      'bar'
    return {
      type: rawType,
      why: spec && typeof spec.why === 'string' ? spec.why : undefined,
      reasons: spec && Array.isArray(spec.reasons) ? spec.reasons : (DEFAULT_CHART_REASONS[rawType] ?? ['по структуре числовых данных']),
      alternatives: spec && Array.isArray(spec.alternatives) ? spec.alternatives : (DEFAULT_CHART_ALTERNATIVES[rawType] ?? DEFAULT_CHART_ALTERNATIVES.bar),
    }
  }

  const isDataPurpose = slide.purpose === 'data' || slide.purpose === 'Данные'
  if (isDataPurpose && units.length > 0 && hasChartData({ content_units: units as SlidePlan['content_units'] })) {
    const combinedText = [slide.title_intent ?? '', slide.key_message ?? '', ...units.map((unit) => unit.text ?? '')].join(' ')
    const inferredType = /динамик|месяц|квартал|год|тренд|темп/i.test(combinedText)
      ? 'line'
      : /дол[яией]|структур|распределен/i.test(combinedText)
        ? 'pie'
        : 'bar'
    return {
      type: inferredType,
      reasons: DEFAULT_CHART_REASONS[inferredType] ?? ['по структуре числовых данных'],
      alternatives: DEFAULT_CHART_ALTERNATIVES[inferredType] ?? DEFAULT_CHART_ALTERNATIVES.bar,
    }
  }

  return null
}

export function parseSlideChartView(slide: ChartSlideLike): SlideChartView | null {
  if (isRecord(slide.chart) && typeof slide.chart.type === 'string' && !slide.chart.type.trim()) {
    return null
  }
  const raw = isRecord(slide.chart) && typeof slide.chart.type === 'string' && slide.chart.type.trim() ? slide.chart : inferChartRecordFromSlide(slide)
  if (!raw || typeof raw.type !== 'string' || !raw.type.trim()) return null
  const type = raw.type.trim()
  const label = chartTypeLabel(type)
  const rawReasons = stringArray(raw.reasons).map(normalizeChartReasonText)
  const reasons = rawReasons.length > 0 ? rawReasons : (DEFAULT_CHART_REASONS[type] ?? [])
  const rawWhy = typeof raw.why === 'string' && raw.why.trim() ? normalizeChartReasonText(raw.why.trim()) : null
  const why = rawWhy ?? (rawReasons.length > 0 ? `${label}: ${rawReasons.join(', ')}.` : `Выбран тип «${label}» по структуре данных.`)
  const seen = new Set<string>([type])
  const options: SlideChartOption[] = [{ type, label, active: true, reasons }]
  if (Array.isArray(raw.alternatives)) {
    for (const item of raw.alternatives) {
      if (!isRecord(item) || typeof item.type !== 'string') continue
      const altType = item.type.trim()
      if (!altType || seen.has(altType)) continue
      seen.add(altType)
      const altReasons = stringArray(item.reasons).map(normalizeChartReasonText)
      options.push({
        type: altType,
        label: chartTypeLabel(altType),
        active: false,
        reasons: altReasons.length > 0 ? altReasons : (DEFAULT_CHART_REASONS[altType] ?? []),
      })
    }
  }
  if (options.length < 2) {
    const fallbacks = DEFAULT_CHART_ALTERNATIVES[type] ?? DEFAULT_CHART_ALTERNATIVES.bar ?? []
    for (const alt of fallbacks) {
      if (seen.has(alt.type)) continue
      seen.add(alt.type)
      options.push({
        type: alt.type,
        label: chartTypeLabel(alt.type),
        active: false,
        reasons: alt.reasons,
      })
    }
  }
  return {
    type,
    label,
    why,
    reasons,
    options,
    viewId: typeof raw.view_id === 'string' ? raw.view_id : null,
    previewArtifactId: typeof raw.preview_artifact_id === 'string' ? raw.preview_artifact_id : null,
  }
}

export function resolveDeckKeyMessage(
  plan: DeckPlan,
  explicit?: string | null,
  sessionBrief?: Record<string, unknown> | null,
): string {
  const ordered = [...plan.slides].sort((a, b) => a.index - b.index)
  const overview = ordered.find((slide) => {
    const purpose: string = slide.purpose
    return purpose === 'overview' || purpose === 'summary'
  })
  const dataSlide = ordered.find((slide) => slide.purpose === 'data')
  const firstTakeaway = ordered.find((slide) => !BOOKENDS.has(slide.purpose))
  const candidates = [
    explicit,
    plan.brief.key_message,
    sessionBrief?.key_message,
    sessionBrief?.purpose,
    overview?.key_message,
    dataSlide?.key_message,
    firstTakeaway?.key_message,
    firstTakeaway?.title_intent,
    plan.objective,
    plan.brief.purpose,
    overview?.title_intent,
    ordered[0]?.key_message,
    ordered[0]?.title_intent,
  ]
  for (const candidate of candidates) {
    if (isMeaningfulKeyMessage(candidate)) return candidate.trim()
  }
  return ''
}

export function availableQuickActions(plan: DeckPlan, slideId: string): QuickActionKind[] {
  const ordered = [...plan.slides].sort((a, b) => a.index - b.index)
  const index = ordered.findIndex((item) => item.id === slideId)
  const slide = ordered[index]
  if (!slide) return []
  const texts = slide.content_units.filter((unit) => isEditableUnit(unit) && Boolean((unit.text ?? '').trim()))
  const actions: QuickActionKind[] = []
  const isBookend = BOOKENDS.has(slide.purpose)
  if (!isBookend && texts.length > 0) actions.push('shorten')
  if (!isBookend && texts.length >= 2) actions.push('split')
  const next = ordered[index + 1]
  if (slide.purpose !== 'title' && next && !BOOKENDS.has(next.purpose)) actions.push('merge_next')
  if (!isBookend && slide.desired_visual !== 'chart' && !slide.chart) actions.push('to_chart')
  if (!isBookend && slide.desired_visual !== 'diagram' && texts.length >= 2) actions.push('to_diagram')
  if (ordered.length > 1 && slide.purpose !== 'title') actions.push('remove')
  return actions
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
  presentation: 'презентация',
}

const UNASSIGNED_SECTION = 'Вне разделов'

export function deckPurposeLabel(purpose: string): string {
  return DECK_PURPOSE_LABEL[purpose] ?? purpose
}

export interface SourcePackLike {
  title_hint?: string | null
  sections?: readonly { id: string; heading: string }[] | null
  blocks?: readonly { id: string; title?: string | null }[] | null
  datasets?: readonly { id: string; title?: string | null; file_name?: string | null; file?: string | null }[] | null
  tables?: readonly { title?: string | null }[] | null
}

export interface SourcePlanLike {
  sections?: readonly { id: string; title: string }[] | null
}

const UNKNOWN_SOURCE_LABEL = 'ваши материалы'
const AUTHOR_NOTES_LABEL = 'Тезисы автора'
const SECTION_NUM_RE = /^sec[-_](\d+)$/i
const DATASET_TOKEN_RE = /^(?:ds:)?(ds[-_][a-z0-9_]+?)(?:-(?:trend|by\d+|corr|dist|top|part|stat|i\d+|row\d+|col\d+)\b.*)?$/i
const TECHNICAL_ID_RE = /^(?:(?:pack|sec|ds|ev|art|slide|tbl|block|unit)[-_:]|[bs]:|[a-z]{1,6}[-_]\d|[0-9a-f]{8,})/i
const TECHNICAL_LABEL_RE = /^(?:(?:art|artifact|pack|sec|ds|ev|blk|block|unit|slide|tbl|src|file|img)[\s_.:-]*[0-9a-f-]{4,}|[0-9a-f]{12,}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}[0-9a-f]{8})$/i

function humanSourceLabel(label: string): string {
  return TECHNICAL_LABEL_RE.test(label.trim()) ? UNKNOWN_SOURCE_LABEL : label
}

function registerSectionAliases(map: Map<string, string>, rawId: string, label: string, overwrite = true) {
  const id = rawId.trim()
  const clean = label.trim()
  if (!id || !clean) return
  if (overwrite || !map.has(id)) map.set(id, clean)
  const match = SECTION_NUM_RE.exec(id)
  if (!match) return
  const num = Number(match[1])
  const padded = String(num).padStart(2, '0')
  for (const alias of [`sec-${num}`, `sec_${num}`, `sec-${padded}`, `sec_${padded}`]) {
    if (overwrite || !map.has(alias)) map.set(alias, clean)
  }
}

export function contentPackSourceMap(pack?: SourcePackLike | null, plan?: SourcePlanLike | null): Map<string, string> {
  const map = new Map<string, string>()

  for (const section of plan?.sections ?? []) {
    if (section.id && section.title?.trim()) {
      registerSectionAliases(map, section.id, section.title, false)
    }
  }

  for (const block of pack?.blocks ?? []) {
    const title = block.title?.trim()
    if (!title) continue
    const secId = block.id.startsWith('s:') ? block.id.slice(2) : block.id.startsWith('b:') ? (block.id.split(':')[1] ?? '') : ''
    if (secId) registerSectionAliases(map, secId, title, false)
  }

  const sections = pack?.sections ?? []
  sections.forEach((section, index) => {
    const heading = section.heading?.trim()
    if (!heading) return
    registerSectionAliases(map, section.id, heading, true)
    map.set(`ev_sec_${index}`, heading)
    const oneBasedPadded = String(index + 1).padStart(2, '0')
    for (const alias of [`sec_${oneBasedPadded}`, `sec-${oneBasedPadded}`]) {
      if (!map.has(alias)) map.set(alias, heading)
    }
  })

  const datasets = pack?.datasets ?? []
  const datasetLabels: string[] = []
  for (const dataset of datasets) {
    const label = dataset.title?.trim() || dataset.file_name?.trim() || dataset.file?.trim()
    if (!label) continue
    datasetLabels.push(label)
    if (dataset.id?.trim()) map.set(dataset.id.trim(), label)
  }
  if (datasetLabels.length === 1 && datasetLabels[0]) {
    map.set('ds:*', datasetLabels[0])
  }

  for (const [index, table] of (pack?.tables ?? []).entries()) {
    const title = table.title?.trim()
    if (title) map.set(`ev_tbl_${index}`, title)
  }

  const metaLabel =
    pack?.title_hint?.trim() ||
    sections.find((section) => section.heading?.trim())?.heading?.trim() ||
    (pack?.blocks ?? []).find((block) => block.title?.trim())?.title?.trim() ||
    (plan?.sections ?? []).find((section) => section.title?.trim())?.title?.trim()
  if (metaLabel) map.set('meta', metaLabel)

  return map
}

export function parseEvidenceRef(raw: string): EvidenceRef {
  if (raw.trim() === 'meta') return { raw, packId: null, sectionId: null, blockId: null, meta: true }
  const [packId = null, second = null, third = null] = raw.split(':')
  if (second === null) return { raw, packId: null, sectionId: null, blockId: null, meta: false }
  if (second === 'meta') return { raw, packId, sectionId: null, blockId: null, meta: true }
  return { raw, packId, sectionId: second, blockId: third, meta: false }
}

function resolveSectionHeading(token: string, raw: string, headings: ReadonlyMap<string, string>): string | null {
  const direct = headings.get(token)?.trim() || headings.get(raw)?.trim()
  if (direct) return direct
  const match = SECTION_NUM_RE.exec(token)
  if (match) {
    const num = Number(match[1])
    const padded = String(num).padStart(2, '0')
    for (const candidate of [`sec-${num}`, `sec_${num}`, `sec-${padded}`, `sec_${padded}`]) {
      const found = headings.get(candidate)?.trim()
      if (found) return found
    }
    if (num >= 1 && !headings.has('sec-1') && headings.has('sec-0')) {
      const zeroBased = headings.get(`sec-${num - 1}`)?.trim()
      if (zeroBased) return zeroBased
    }
  }
  if (/^sec[-_]notes\b/i.test(token) || raw.includes('user:notes')) {
    return AUTHOR_NOTES_LABEL
  }
  if (!TECHNICAL_ID_RE.test(token) && /[а-яё]/i.test(token)) {
    return token
  }
  return null
}

export function slideSources(evidenceIds: readonly string[], headings: ReadonlyMap<string, string>): SlideSource[] {
  const sources = new Map<string, SlideSource>()
  for (const raw of evidenceIds) {
    const trimmed = raw.trim()
    if (!trimmed) continue
    const ref = parseEvidenceRef(trimmed)
    const token = (ref.sectionId ?? trimmed).trim()
    const datasetId = DATASET_TOKEN_RE.exec(token)?.[1] ?? DATASET_TOKEN_RE.exec(trimmed)?.[1] ?? null

    let key: string
    let label: string

    if (ref.meta || token === 'meta' || trimmed.endsWith(':meta')) {
      const metaHeading = headings.get('meta')?.trim()
      key = metaHeading ? 'meta' : 'materials'
      label = humanSourceLabel(metaHeading || UNKNOWN_SOURCE_LABEL)
    } else if (datasetId) {
      const dsHeading = headings.get(token)?.trim() || headings.get(datasetId)?.trim() || headings.get('ds:*')?.trim()
      key = dsHeading ? datasetId : 'materials'
      label = humanSourceLabel(dsHeading || UNKNOWN_SOURCE_LABEL)
    } else {
      const resolved = resolveSectionHeading(token, trimmed, headings)
      key = resolved ? token : 'materials'
      label = humanSourceLabel(resolved || UNKNOWN_SOURCE_LABEL)
    }

    const dedupeKey = [...sources.values()].find((item) => item.label === label)?.key ?? key
    const existing = sources.get(dedupeKey)
    if (existing) existing.refs.push(raw)
    else sources.set(dedupeKey, { key: dedupeKey, label, refs: [raw] })
  }
  return [...sources.values()]
}

export function slideSourceKinds(slide: Pick<SlidePlan, 'evidence_ids' | 'content_units'>): SourceKind[] {
  const found = new Set<SourceKind>()
  for (const raw of slide.evidence_ids) {
    const ref = parseEvidenceRef(raw)
    const token = (ref.sectionId ?? raw).trim()
    if (ref.meta || token === 'meta') continue
    found.add(/^(?:ds:)?ds[-_]/i.test(token) ? 'table' : 'text')
  }
  if (slide.content_units.some((unit) => unitOrigin(unit) === 'web')) found.add('web')
  return SOURCE_KIND_ORDER.filter((kind) => found.has(kind))
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
  const mergedHeadings = contentPackSourceMap(null, plan)
  for (const [key, value] of headings) mergedHeadings.set(key, value)
  const firstSection = new Map<string, { id: string; title: string }>()
  for (const section of sections) {
    for (const slideId of section.slide_ids) if (!firstSection.has(slideId)) firstSection.set(slideId, section)
  }

  const rows: PlanRow[] = slides.map((slide, position) => {
    const section = firstSection.get(slide.id) ?? null
    return {
      id: slide.id,
      number: formatIndex(position + 1),
      purpose: slideRoleLabel(slide),
      title: slide.title_intent.trim() || 'Без заголовка',
      titleIntent: slide.title_intent,
      keyMessage: slide.key_message,
      idea: slideIdea(slide, plan.objective),
      visual: plannedVisual(slide) ?? '',
      sources: slideSources(slide.evidence_ids, mergedHeadings),
      sectionId: section?.id ?? null,
      sectionTitle: section?.title ?? null,
      chart: parseSlideChartView(slide),
      supportsKeyMessage: typeof slide.supports_key_message === 'boolean' ? slide.supports_key_message : null,
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

export interface SlidePatchEntry {
  slideId: string
  body: Record<string, unknown>
}

const DRAFT_STORAGE_PREFIX = 'deckdna.plan-draft.v1:'

export function syncPlanDraftToStorage(draftKey: string, basePlan: DeckPlan, activePlan: DeckPlan): void {
  if (typeof window === 'undefined') return
  try {
    const baseId = basePlan.slides.map((slide) => slide.id).join('|')
    window.localStorage.setItem(
      `${DRAFT_STORAGE_PREFIX}${draftKey}`,
      JSON.stringify({ baseId, plan: activePlan }),
    )
  } catch {
    return
  }
}

function slideBullets(slide: SlidePlan): string[] {
  return slide.content_units.filter(isEditableUnit).map((unit) => unit.text ?? '')
}

function sameStringList(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((item, index) => item === right[index])
}

export function collectSlidePatches(basePlan: DeckPlan, activePlan: DeckPlan): SlidePatchEntry[] {
  const baseById = new Map(basePlan.slides.map((slide) => [slide.id, slide]))
  const patches: SlidePatchEntry[] = []

  for (const slide of activePlan.slides) {
    const baseSlide = baseById.get(slide.id)
    if (!baseSlide) continue
    const body: Record<string, unknown> = {}

    if (slide.title_intent !== baseSlide.title_intent) {
      body.title = slide.title_intent
      body.title_intent = slide.title_intent
    }
    if (slide.key_message !== baseSlide.key_message) {
      body.key_message = slide.key_message
    }
    const nextBullets = slideBullets(slide)
    const prevBullets = slideBullets(baseSlide)
    if (!sameStringList(nextBullets, prevBullets)) {
      body.bullets = nextBullets
      body.content_units = slide.content_units
    }
    if (slide.purpose !== baseSlide.purpose) {
      body.purpose = slide.purpose
    }
    if (slide.desired_visual !== baseSlide.desired_visual) {
      body.desired_visual = slide.desired_visual
    }
    if (slide.index !== baseSlide.index) {
      body.index = slide.index
    }

    if (Object.keys(body).length > 0) {
      patches.push({ slideId: slide.id, body })
    }
  }

  return patches
}

