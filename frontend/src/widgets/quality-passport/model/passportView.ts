import { ruleMeta } from '@/entities/audit'
import type { PassportModelProfile, QualityPassport } from '@/entities/passport'
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
  anchor: 'Якоря',
  anchors: 'Якоря',
  anchor_compliance: 'Якоря',
}

const USAGE_LABEL: Record<string, string> = {
  prompt_tokens: 'Токены запроса',
  completion_tokens: 'Токены ответа',
  total_tokens: 'Токенов всего',
  tokens: 'Токенов',
  model_calls: 'Вызовов модели',
  calls: 'Вызовов модели',
}

const PROMPT_LIMIT = 6

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

function humanize(key: string): string {
  const text = key.replace(/[_.:/-]+/g, ' ').trim()
  return text.charAt(0).toUpperCase() + text.slice(1)
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
      hint: roundTrip === null ? 'проверка не проводилась' : roundTrip ? 'round-trip без потерь' : 'round-trip с ошибками',
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
      label: STYLE_LABEL[key] ?? humanize(key),
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
      label: stageLabel(stage) ?? stage,
      seconds: formatSeconds(seconds),
      fraction: Math.min(1, seconds / budgetSeconds),
    })),
  }
}

function usageView(passport: QualityPassport): UsageView[] | null {
  if (!passport.usage) return null
  return passport.usage.map(({ key, value }) => ({ key, label: USAGE_LABEL[key] ?? humanize(key), value: formatNumber(value) }))
}

function modelLabel(profile: PassportModelProfile): string {
  const model = profile.modelId ?? 'модель без идентификатора'
  return profile.role ? `${profile.role}: ${model}` : model
}

function provenanceRows(passport: QualityPassport, context: PassportContext): ProvenanceRow[] {
  const { provenance } = passport
  const rows: ProvenanceRow[] = []
  const skill = provenance.skillVersion
  const server = context.serverSkillVersion ?? null

  rows.push({
    key: 'skill',
    label: 'Скилл',
    value: skill ?? 'не указан',
    muted: !skill,
    note: skill && server && server !== skill ? `сейчас на сервере ${server} — прогон собран предыдущей версией` : undefined,
  })
  if (provenance.pipelineVersion) rows.push({ key: 'pipeline', label: 'Пайплайн', value: provenance.pipelineVersion })
  if (context.planner) rows.push({ key: 'planner', label: 'Планировщик', value: context.planner })

  const prompts = Object.entries(provenance.promptVersions)
  rows.push(
    prompts.length > 0
      ? {
          key: 'prompts',
          label: 'Промпты',
          value: prompts
            .slice(0, PROMPT_LIMIT)
            .map(([name, version]) => `${name} ${version}`)
            .join(' · '),
          note: prompts.length > PROMPT_LIMIT ? `и ещё ${prompts.length - PROMPT_LIMIT}` : undefined,
        }
      : { key: 'prompts', label: 'Промпты', value: 'не использовались', muted: true },
  )

  rows.push(
    provenance.modelProfiles.length > 0
      ? { key: 'models', label: 'Модели', value: provenance.modelProfiles.map(modelLabel).join(' · ') }
      : { key: 'models', label: 'Модели', value: 'без модели', muted: true },
  )

  for (const [path, hash] of Object.entries(provenance.configHashes)) {
    rows.push({ key: `config:${path}`, label: 'Конфиг', value: `${path} · sha ${formatShortHash(hash)}` })
  }
  return rows
}

export function buildPassportView(passport: QualityPassport, context: PassportContext): PassportView {
  return {
    generatedAt: passport.generatedAt,
    schemaVersion: passport.schemaVersion,
    skillVersion: passport.provenance.skillVersion,
    pipelineVersion: passport.provenance.pipelineVersion,
    proof: proofCells(passport, context.fallbackPei ?? null),
    style: styleView(passport),
    sources: sourcesView(passport),
    readability: { ...passport.readability },
    issues: issuesView(passport),
    timing: timingView(passport, context.budgetSeconds),
    usage: usageView(passport),
    fallbacks: passport.fallbacks,
    autoFixes: passport.autoFixes.map((fix) => ({
      label: ruleMeta(fix.ruleCode).name,
      detail: fix.count === null ? null : `исправлено ${formatNumber(fix.count)}`,
    })),
    provenance: provenanceRows(passport, context),
  }
}
