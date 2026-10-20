import { ruleMeta } from '@/entities/audit'
import type { QualityPassport } from '@/entities/passport'
import { stageLabel } from '@/entities/generation'
import { PEI_MAX } from '@/entities/variant'
import { formatClock, formatNumber, formatPercent, formatSeconds, formatShortHash } from '@/shared/lib/format'

export type ProofTone = 'ok' | 'warn' | 'error' | 'neutral' | 'unknown'

export interface ProofCell {
  key: 'roundTrip' | 'ooxml' | 'nativeText' | 'rasterSlides' | 'pei'
  value: string
  label: string
  hint: string
  tone: ProofTone
}

export interface ScoreView {
  key: string
  label: string
  value: string
  fraction: number
}

export type StyleFidelityView = { status: 'soon' } | { status: 'ready'; scores: ScoreView[] }

export interface SourcesView {
  supported: number | null
  unsupported: number | null
  total: number | null
  numbersVerified: number | null
  numbersFailed: number | null
}

export interface OriginShareView {
  key: 'user' | 'web' | 'model'
  label: string
  count: number
  fraction: number
}

export interface ResearchView {
  origins: OriginShareView[]
  sources: number | null
  mode: string | null
  seconds: string | null
}

export interface ReadabilityView {
  contrastFailures: number | null
  overflowCount: number | null
  avgOccupancy: number | null
}

export interface IssueCountView {
  severity: 'blocker' | 'error' | 'warning' | 'info'
  count: number
}

export interface IssuesView {
  counts: IssueCountView[]
  total: number
  unresolved: number | null
}

export interface StageView {
  key: string
  label: string
  seconds: string
  fraction: number
}

export interface TimingView {
  total: string | null
  totalSeconds: number | null
  budget: string
  fraction: number
  withinBudget: boolean | null
  stages: StageView[]
}

export interface UsageView {
  key: string
  label: string
  value: string
}

export interface ProvenanceRow {
  key: string
  label: string
  value: string
  note?: string
  muted?: boolean
}

export interface PassportView {
  generatedAt: string | null
  schemaVersion: string | null
  skillVersion: string | null
  pipelineVersion: string | null
  proof: ProofCell[]
  style: StyleFidelityView
  sources: SourcesView | null
  research: ResearchView | null
  readability: ReadabilityView
  issues: IssuesView
  timing: TimingView
  usage: UsageView[] | null
  fallbacks: { label: string; detail: string | null }[]
  autoFixes: { label: string; detail: string | null }[]
  provenance: ProvenanceRow[]
}

export interface PassportContext {
  budgetSeconds: number
  planner?: string | null
  serverSkillVersion?: string | null
  fallbackPei?: number | null
}

const STYLE_LABEL: Record<string, string> = {
  palette: 'Палитра',
  palette_compliance: 'Палитра',
  font: 'Шрифты',
  fonts: 'Шрифты',
  font_compliance: 'Шрифты',
  layout: 'Макеты',
  layouts: 'Макеты',
  layout_compliance: 'Макеты',
  layout_origin_compliance: 'Макеты',
  hierarchy: 'Иерархия',
  hierarchy_compliance: 'Иерархия',
  anchor: 'Якоря',
  anchors: 'Якоря',
  anchor_compliance: 'Якоря',
  overall: 'Итог',
  custom_score: 'Доп. оценка',
}

const AUTO_FIX_LABEL: Record<string, string> = {
  font_family_replace_with_dna: 'Шрифты приведены к шаблону',
  font_size_snap_to_nearest: 'Кегль приведён к шкале шаблона',
  color_snap_to_dna_palette: 'Цвета приведены к палитре шаблона',
  contrast_text_recolor: 'Контраст текста подтянут до нормы',
  textbox_reflow_or_shrink: 'Переполненные рамки подогнаны',
  bullet_overflow_truncate_note: 'Лишние пункты перенесены в заметки',
  overflow_multi_column_split: 'Плотные списки разложены в колонки',
  slide_split_continuation: 'Перегруженные слайды разбиты на продолжение',
  chart_readability_fix: 'Подписи графиков выровнены по кеглю',
  table_overflow_fix: 'Таблицы подогнаны под сетку слайда',
}

const FALLBACK_LABEL: Record<string, string> = {
  layout_overflow: 'Запасной макет при переполнении',
  missing_asset: 'Заглушка вместо отсутствующего изображения',
  unsupported_chart: 'Таблица вместо неподдерживаемого графика',
  llm_timeout: 'Сборка по правилам после ожидания ответа',
  llm_invalid_json: 'Повторная сборка по правилам',
  llm_unavailable: 'Детерминированный режим по правилам',
  font_substitution: 'Подстановка доступного шрифта шаблона',
  render_failed: 'Резервная отрисовка слайда',
  deterministic: 'По правилам',
  standard: 'Стандартный режим',
  passed: 'Пройдено',
  completed: 'Пройдено',
  failed: 'Не пройдено',
  'metrics.content_support.numbers_verified': 'Сверка чисел с источником',
  'metrics.content_support.numbers_failed': 'Числа без подтверждения в источнике',
  numbers_verified: 'Сверка чисел с источником',
  numbers_failed: 'Числа без подтверждения в источнике',
}

const FALLBACK_DETAIL: Record<string, string> = {
  numeric_presence_only: 'Сверено только наличие числа в исходном тексте, без пересчёта формул.',
}

function localizeFallbackDetail(detail: string): string | null {
  const trimmed = detail.trim()
  if (!trimmed) return null
  const lowered = trimmed.toLowerCase()
  if (FALLBACK_DETAIL[lowered]) return FALLBACK_DETAIL[lowered]
  if (/[а-яё]/i.test(trimmed)) return trimmed
  if (/^[\x20-\x7e]+$/.test(trimmed)) {
    const token = translatePassportToken(trimmed)
    return token === trimmed ? 'Подробности — в техническом приложении паспорта.' : token
  }
  return trimmed
}

const PEI_MEANING: Record<number, string> = {
  0: 'не открывается или слайды-картинки',
  1: 'правится только часть текста',
  2: 'правятся текст и фигуры',
  3: 'правится всё, нативных таблиц и графиков нет',
  4: 'правится всё, таблицы и графики нативные',
  5: 'всё нативное и привязано к макетам',
}

export function peiHint(level: number | null): string {
  const meaning = level === null ? undefined : PEI_MEANING[level]
  return meaning ? `шкала 0–5; ${level} — ${meaning}` : 'шкала 0–5: чем выше, тем больше правится в PowerPoint'
}

export function translatePassportToken(raw: string | null | undefined): string {
  if (!raw) return 'По правилам'
  const trimmed = raw.trim()
  const lower = trimmed.toLowerCase()
  if (FALLBACK_LABEL[lower]) return FALLBACK_LABEL[lower]
  if (AUTO_FIX_LABEL[lower]) return AUTO_FIX_LABEL[lower]
  if (lower.includes('user_edited')) return 'Отредактирован вручную'
  if (lower.includes('deterministic')) return 'По правилам'
  if (lower.includes('hybrid')) return 'Смешанный'
  if (lower === 'standard') return 'Стандартный'
  if (lower === 'strict') return 'Строгий'
  if (lower === 'passed' || lower === 'completed') return 'Пройдено'
  if (lower === 'failed') return 'Есть замечания'
  return trimmed
}

function localizeStyleLabel(key: string): string {
  const normalized = key.trim().toLowerCase()
  if (STYLE_LABEL[normalized]) return STYLE_LABEL[normalized]
  const text = key.replace(/[_.:/-]+/g, ' ').trim()
  if (/^[a-z0-9\s]+$/i.test(text)) return 'Доп. показатель'
  return text.charAt(0).toUpperCase() + text.slice(1)
}

function localizeFixLabel(code: string): string {
  const normalized = code.trim().toLowerCase()
  if (AUTO_FIX_LABEL[normalized]) return AUTO_FIX_LABEL[normalized]
  if (FALLBACK_LABEL[normalized]) return FALLBACK_LABEL[normalized]
  const metaName = ruleMeta(code).name
  if (metaName !== code) return metaName
  if (/^[a-z0-9_.:/-]+$/i.test(code)) return 'Автоматическая правка вёрстки'
  return metaName
}

function count(value: number | null): string {
  return value === null ? '—' : formatNumber(value)
}

function proofCells(passport: QualityPassport, fallbackPei: number | null): ProofCell[] {
  const { validity, editability } = passport
  const roundTrip = validity.roundTripOk ?? validity.opensCleanly
  const pei = editability.peiLevel ?? fallbackPei
  const ratio = editability.nativeTextRatio
  const raster = editability.rasterOnlySlides
  const ooxml = validity.ooxmlErrors

  return [
    {
      key: 'roundTrip',
      value: roundTrip === null ? '—' : roundTrip ? '✓' : '✕',
      label: roundTrip === false ? 'Файл не открылся' : 'Файл повторно открыт',
      hint: roundTrip === null ? 'проверка не проводилась' : roundTrip ? 'открывается без потерь' : 'открытие с ошибками',
      tone: roundTrip === null ? 'unknown' : roundTrip ? 'ok' : 'error',
    },
    {
      key: 'ooxml',
      value: count(ooxml),
      label: 'ошибок OOXML',
      hint: 'валидация схемы',
      tone: ooxml === null ? 'unknown' : ooxml === 0 ? 'neutral' : 'error',
    },
    {
      key: 'nativeText',
      value: ratio === null ? '—' : formatPercent(ratio),
      label: 'нативного текста',
      hint: 'текстовые фигуры среди всех объектов слайдов',
      tone: ratio === null ? 'unknown' : 'neutral',
    },
    {
      key: 'rasterSlides',
      value: count(raster),
      label: 'слайдов-картинок',
      hint: raster === 0 ? 'графики и таблицы — объекты' : 'слайды, собранные одной картинкой',
      tone: raster === null ? 'unknown' : raster === 0 ? 'neutral' : 'warn',
    },
    {
      key: 'pei',
      value: pei === null ? '—' : `${pei}/${PEI_MAX}`,
      label: 'редактируемость PEI',
      hint: peiHint(pei),
      tone: pei === null ? 'unknown' : 'neutral',
    },
  ]
}

function styleView(passport: QualityPassport): StyleFidelityView {
  if (!passport.styleFidelity) return { status: 'soon' }
  return {
    status: 'ready',
    scores: passport.styleFidelity.map(({ key, value }) => ({
      key,
      label: localizeStyleLabel(key),
      value: value.toFixed(2),
      fraction: Math.max(0, Math.min(1, value)),
    })),
  }
}

function sourcesView(passport: QualityPassport): SourcesView | null {
  const { supportedClaims: supported, unsupportedClaims: unsupported, numbersVerified, numbersFailed } = passport.contentSupport
  if (supported === null && unsupported === null && numbersVerified === null && numbersFailed === null) return null
  const total = supported !== null && unsupported !== null ? supported + unsupported : null
  return { supported, unsupported, total, numbersVerified, numbersFailed }
}

function issuesView(passport: QualityPassport): IssuesView {
  const { blocker, error, warning, info, unresolved } = passport.issues
  const counts: IssueCountView[] = [
    { severity: 'blocker', count: blocker },
    { severity: 'error', count: error },
    { severity: 'warning', count: warning },
    { severity: 'info', count: info },
  ]
  return { counts, total: blocker + error + warning + info, unresolved }
}

function timingView(passport: QualityPassport, budgetSeconds: number): TimingView {
  const total = passport.timings.totalSeconds
  return {
    total: total === null ? null : formatSeconds(total),
    totalSeconds: total,
    budget: formatClock(budgetSeconds),
    fraction: total === null ? 0 : Math.min(1, total / budgetSeconds),
    withinBudget: total === null ? null : total <= budgetSeconds,
    stages: passport.timings.perStage.map(({ stage, seconds }) => ({
      key: stage,
      label: stageLabel(stage) ?? translatePassportToken(stage),
      seconds: formatSeconds(seconds),
      fraction: Math.min(1, seconds / budgetSeconds),
    })),
  }
}

function provenanceRows(passport: QualityPassport, context: PassportContext): ProvenanceRow[] {
  const { provenance } = passport
  const rows: ProvenanceRow[] = []
  const skill = provenance.skillVersion
  const server = context.serverSkillVersion ?? null

  rows.push({
    key: 'skill',
    label: 'Версия правил',
    value: skill ?? 'не указана',
    muted: !skill,
    note: skill && server && server !== skill ? `сейчас на сервере ${server} — прогон собран предыдущей версией` : undefined,
  })
  if (context.planner) {
    rows.push({ key: 'planner', label: 'Планировщик', value: translatePassportToken(context.planner) })
  }

  const hasExtendedCheck = provenance.modelProfiles.length > 0 || Object.keys(provenance.promptVersions).length > 0
  rows.push({
    key: 'mode',
    label: 'Режим проверки',
    value: hasExtendedCheck ? 'Расширенный (правила и смысл)' : 'Стандартный (по правилам шаблона)',
  })

  const configEntries = Object.entries(provenance.configHashes)
  const seenConfigHashes = new Set<string>()
  const uniqueConfigs = configEntries.filter(([, hash]) => {
    const short = formatShortHash(hash)
    if (seenConfigHashes.has(short)) return false
    seenConfigHashes.add(short)
    return true
  })
  const CONFIG_LABELS: Record<string, string> = {
    'configs/generation.default.yaml': 'Конфигурация сборки',
    'configs/audit.default.yaml': 'Конфигурация проверки',
  }
  for (const [path, hash] of uniqueConfigs) {
    const label = uniqueConfigs.length > 1 ? (CONFIG_LABELS[path] ?? 'Конфигурация') : 'Конфигурация'
    rows.push({ key: `config:${path}`, label, value: `sha ${formatShortHash(hash)}` })
  }
  return rows
}

const ORIGIN_LABEL: Record<OriginShareView['key'], string> = { user: 'ваш контент', web: 'интернет', model: 'дополнено по смыслу' }

const FILL_LABEL: Record<string, string> = { expand: 'дополнить', shrink: 'сохранить как есть' }

export function researchView(passport: QualityPassport): ResearchView | null {
  const { research } = passport
  if (!research) return null
  const origin = research.origin
  const total = origin ? origin.user + origin.web + origin.model : 0
  const origins: OriginShareView[] = origin
    ? (['user', 'web', 'model'] as const).map((key) => ({ key, label: ORIGIN_LABEL[key], count: origin[key], fraction: total > 0 ? origin[key] / total : 0 }))
    : []
  return {
    origins,
    sources: research.sources,
    mode: research.contentFill ? (FILL_LABEL[research.contentFill] ?? translatePassportToken(research.contentFill)) : null,
    seconds: research.seconds === null ? null : formatSeconds(research.seconds),
  }
}

export function buildPassportView(passport: QualityPassport, context: PassportContext): PassportView {
  return {
    generatedAt: passport.generatedAt,
    schemaVersion: passport.schemaVersion,
    skillVersion: passport.provenance.skillVersion,
    pipelineVersion: null,
    proof: proofCells(passport, context.fallbackPei ?? null),
    style: styleView(passport),
    sources: sourcesView(passport),
    research: researchView(passport),
    readability: { ...passport.readability },
    issues: issuesView(passport),
    timing: timingView(passport, context.budgetSeconds),
    usage: null,
    fallbacks: passport.fallbacks.map((item) => ({
      label: localizeFixLabel(item.label),
      detail: item.detail ? localizeFallbackDetail(item.detail) : null,
    })),
    autoFixes: passport.autoFixes.map((fix) => ({
      label: localizeFixLabel(fix.ruleCode),
      detail: fix.count === null ? null : `исправлено ${formatNumber(fix.count)}`,
    })),
    provenance: provenanceRows(passport, context),
  }
}
