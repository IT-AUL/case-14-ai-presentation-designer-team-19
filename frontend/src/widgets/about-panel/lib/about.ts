import { CATEGORY_LABEL, listRules } from '@/entities/audit'
import type { CheckKind, RuleCategory, RuleMeta } from '@/entities/audit'
import { isCapabilityAvailable } from '@/entities/system'
import type { Capabilities, SkillManifest, VersionInfo } from '@/entities/system'

export interface InfoRow {
  label: string
  value: string
}

const VERSION_LABEL: Record<string, string> = {
  service: 'Сервис',
  version: 'Версия сервиса',
  api_version: 'API',
  pipeline_version: 'Конвейер',
  skill_version: 'Скилл',
  parser_version: 'Парсер шаблона',
  planner_version: 'Планировщик',
  rule_version: 'Правила аудита',
  git_sha: 'Коммит',
  commit: 'Коммит',
  build: 'Сборка',
  environment: 'Окружение',
}

const VERSION_ORDER = Object.keys(VERSION_LABEL)

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isPrimitive(value: unknown): value is string | number | boolean {
  return typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
}

function orderIndex(key: string): number {
  const index = VERSION_ORDER.indexOf(key)
  return index < 0 ? VERSION_ORDER.length : index
}

export function versionRows(version: VersionInfo | Record<string, unknown> | null | undefined): InfoRow[] {
  if (!isRecord(version)) return []
  return Object.entries(version)
    .filter((entry): entry is [string, string | number | boolean] => isPrimitive(entry[1]) && String(entry[1]).trim() !== '')
    .sort(([a], [b]) => orderIndex(a) - orderIndex(b))
    .map(([key, value]) => ({ label: VERSION_LABEL[key] ?? key, value: String(value) }))
}

export type CapabilityState = 'on' | 'soon'

export interface CapabilityRow {
  id: string
  label: string
  description: string
  state: CapabilityState
}

interface CapabilitySpec {
  id: string
  label: string
  paths: readonly string[]
  on: string
  off: string
}

const CAPABILITY_SPECS: readonly CapabilitySpec[] = [
  { id: 'pptx', label: 'Экспорт PPTX', paths: ['exporters.pptx', 'exports.pptx', 'export_formats.pptx', 'exports.formats.pptx'], on: 'нативные объекты, редактируемый файл', off: 'сервис не заявил формат' },
  { id: 'pdf', label: 'Экспорт PDF', paths: ['exporters.pdf', 'exports.pdf', 'export_formats.pdf', 'exports.formats.pdf'], on: 'рендер колоды', off: 'сервис не заявил формат' },
  { id: 'html', label: 'Экспорт HTML', paths: ['features.html_export', 'exporters.html', 'exports.html', 'export_formats.html', 'exports.formats.html'], on: 'разметка, не скриншоты', off: 'в разработке' },
  {
    id: 'passport',
    label: 'Паспорт качества',
    paths: ['exporters.quality_passport', 'exports.quality_passport', 'export_formats.quality_passport', 'exports.formats.quality_passport'],
    on: 'метрики, версии и происхождение колоды',
    off: 'в разработке',
  },
  {
    id: 'contextual',
    label: 'Контекстные проверки моделью',
    paths: ['features.contextual_audit', 'audit.contextual_rules', 'audit.contextual', 'contextual_audit'],
    on: 'нужна сессия провайдера моделей',
    off: 'в разработке',
  },
  { id: 'async', label: 'Статус каждого варианта', paths: ['features.async_generation', 'generation.async_jobs', 'async_jobs'], on: 'варианты готовятся по очереди', off: 'сейчас три варианта готовы вместе' },
  { id: 'sse', label: 'Стриминг прогресса этапов', paths: ['features.sse_progress', 'events.sse', 'sse'], on: 'события по этапам конвейера', off: 'сейчас только статус задачи' },
  { id: 'previews', label: 'PNG-превью слайдов', paths: ['features.png_previews', 'previews.slide_png', 'slide_previews'], on: 'картинки слайдов от сервиса', off: 'превью строятся из PDF в браузере' },
  { id: 'plan', label: 'План до вёрстки', paths: ['features.plan_only', 'generation.plan_only', 'plan_only'], on: 'план можно поправить до вёрстки', off: 'сейчас план показывается после сборки' },
]

export function capabilityRows(capabilities: Capabilities | null | undefined): CapabilityRow[] {
  return CAPABILITY_SPECS.map((spec) => {
    const available = spec.paths.some((path) => isCapabilityAvailable(capabilities, path))
    return { id: spec.id, label: spec.label, description: available ? spec.on : spec.off, state: available ? 'on' : 'soon' }
  })
}

function readPath(source: unknown, path: string): unknown {
  let current = source
  for (const segment of path.split('.')) {
    if (!isRecord(current)) return undefined
    current = current[segment]
  }
  return current
}

export function formatRows(capabilities: Capabilities | null | undefined): InfoRow[] {
  const specs: [string, string][] = [
    ['Форматы шаблона', 'uploads.template_extensions'],
    ['Форматы контента', 'uploads.content_extensions'],
  ]
  return specs.flatMap(([label, path]) => {
    const value = readPath(capabilities, path)
    return Array.isArray(value) && value.length > 0 ? [{ label, value: value.map(String).join(' ') }] : []
  })
}

export interface ManifestItem {
  name: string
  version: string | null
  detail: string | null
}

export interface ManifestGroup {
  key: string
  title: string
  items: ManifestItem[]
}

export interface ManifestView {
  name: string | null
  version: string | null
  description: string | null
  meta: InfoRow[]
  groups: ManifestGroup[]
}

const MANIFEST_GROUP_TITLE: Record<string, string> = {
  skills: 'Скиллы',
  agents: 'Агенты',
  prompts: 'Промпты',
  prompt_versions: 'Промпты',
  configs: 'Конфиги',
  config_hashes: 'Конфиги',
  tools: 'Инструменты',
  models: 'Модели',
  inputs: 'Входы',
  outputs: 'Выходы',
  provenance: 'Версии компонентов',
}

const MANIFEST_ORDER = Object.keys(MANIFEST_GROUP_TITLE)
const MANIFEST_HEAD = new Set(['name', 'version', 'description'])

function text(value: unknown): string | null {
  return isPrimitive(value) && String(value).trim() !== '' ? String(value) : null
}

function itemDetail(record: Record<string, unknown>): string | null {
  const parts: string[] = []
  const type = text(record.type)
  if (type) parts.push(type)
  if (Array.isArray(record.formats)) parts.push(record.formats.map(String).join(', '))
  const count = text(record.count)
  if (count) parts.push(`× ${count}`)
  const path = text(record.path)
  if (path && path !== text(record.name)) parts.push(path)
  if (record.required === true) parts.push('обязательно')
  const description = text(record.description)
  if (description) parts.push(description)
  return parts.length > 0 ? parts.join(' · ') : null
}

function toItem(value: unknown): ManifestItem | null {
  if (isPrimitive(value)) return { name: String(value), version: null, detail: null }
  if (!isRecord(value)) return null
  const name = text(value.name) ?? text(value.id) ?? text(value.path) ?? text(value.file)
  if (!name) return null
  return { name, version: text(value.version), detail: itemDetail(value) }
}

function groupItems(value: unknown): ManifestItem[] {
  if (Array.isArray(value)) return value.map(toItem).filter((item): item is ManifestItem => item !== null)
  if (!isRecord(value)) return []
  return Object.entries(value).flatMap(([key, entry]) => {
    if (isPrimitive(entry)) return [{ name: key, version: String(entry), detail: null }]
    if (isRecord(entry)) return [{ name: key, version: text(entry.version), detail: itemDetail(entry) }]
    if (Array.isArray(entry)) return [{ name: key, version: null, detail: entry.map(String).join(', ') }]
    return []
  })
}

function manifestOrder(key: string): number {
  const index = MANIFEST_ORDER.indexOf(key)
  return index < 0 ? MANIFEST_ORDER.length : index
}

export function describeManifest(manifest: SkillManifest | null | undefined): ManifestView {
  if (!isRecord(manifest)) return { name: null, version: null, description: null, meta: [], groups: [] }
  const meta: InfoRow[] = []
  const groups: ManifestGroup[] = []
  for (const [key, value] of Object.entries(manifest)) {
    if (MANIFEST_HEAD.has(key)) continue
    if (isPrimitive(value)) {
      if (String(value).trim() !== '') meta.push({ label: key, value: String(value) })
      continue
    }
    const items = groupItems(value)
    if (items.length > 0) groups.push({ key, title: MANIFEST_GROUP_TITLE[key] ?? key, items })
  }
  groups.sort((a, b) => manifestOrder(a.key) - manifestOrder(b.key))
  return { name: text(manifest.name), version: text(manifest.version), description: text(manifest.description), meta, groups }
}

export interface RuleView {
  code: string
  name: string
  kind: CheckKind
  autoFix: string | null
}

export interface RuleGroupView {
  category: RuleCategory
  label: string
  rules: RuleView[]
}

export function ruleKind(category: RuleCategory): CheckKind {
  return category === 'meaning' ? 'N' : 'D'
}

function metaKind(meta: RuleMeta): CheckKind {
  if (typeof meta.deterministic === 'boolean') return meta.deterministic ? 'D' : 'N'
  return ruleKind(meta.category)
}

export function ruleGroups(rules: readonly [string, RuleMeta][] = listRules()): RuleGroupView[] {
  const categories = Object.keys(CATEGORY_LABEL) as RuleCategory[]
  return categories
    .map((category) => ({
      category,
      label: CATEGORY_LABEL[category],
      rules: rules
        .filter(([, meta]) => meta.category === category)
        .map(([code, meta]) => ({ code, name: meta.name, kind: metaKind(meta), autoFix: meta.autoFix ?? null })),
    }))
    .filter((group) => group.rules.length > 0)
}

export function countRules(groups: readonly RuleGroupView[]): Record<CheckKind, number> {
  const counts: Record<CheckKind, number> = { D: 0, N: 0 }
  for (const group of groups) for (const rule of group.rules) counts[rule.kind] += 1
  return counts
}
