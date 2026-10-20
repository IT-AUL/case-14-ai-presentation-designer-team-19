import type { SlidePlan } from '../model/types'

export type SufficiencyStatus = 'short' | 'ok' | 'excess'

export interface PlanSufficiency {
  status: SufficiencyStatus
  contentSlides: number | null
  targetSlides: number | null
}

export interface ResearchSource {
  id: string
  title: string
  url: string | null
  site: string | null
  accessedAt: string | null
  facts: number | null
}

export interface PlanResearch {
  queries: string[]
  sources: ResearchSource[]
  seconds: number | null
}

export interface FactsByOrigin {
  user: number
  web: number
  model: number
}

export interface ContentFillSummary {
  capacity: number | null
  requested: number | null
  targetSlides: number | null
  factsByOrigin: FactsByOrigin | null
  webSources: ResearchSource[]
  shortageReason: string | null
}

export type UnitOrigin = 'user' | 'web' | 'model'

export interface PlanProgress {
  stage: string | null
  queries: string[]
  sources: ResearchSource[]
  slides: SlidePlan[]
}

export const EMPTY_PROGRESS: PlanProgress = { stage: null, queries: [], sources: [], slides: [] }

const ORIGIN_ALIASES: Record<string, UnitOrigin> = {
  user: 'user',
  content: 'user',
  pack: 'user',
  source: 'user',
  web: 'web',
  internet: 'web',
  research: 'web',
  search: 'web',
  model: 'model',
  llm: 'model',
  knowledge: 'model',
  generated: 'model',
}

const SHORTAGE_REASON_KEYS = [
  'shortage_reason',
  'short_reason',
  'slide_count_reason',
  'slides_reason',
  'capacity_reason',
  'why_shorter',
  'why_fewer_slides',
  'fewer_slides_reason',
  'shrink_reason',
  'shortage_note',
  'shortage_message',
] as const

const CONTENT_FILL_REASON_KEYS = [
  ...SHORTAGE_REASON_KEYS,
  'reason',
  'explanation',
  'note',
  'message',
] as const

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function firstNumber(record: Record<string, unknown>, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'number' && Number.isFinite(value)) return value
  }
  return null
}

function firstString(record: Record<string, unknown>, keys: readonly string[]): string | null {
  for (const key of keys) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

function isHumanSentence(value: string | null): value is string {
  if (!value) return false
  if (/^[a-z0-9_.:-]+$/i.test(value)) return false
  return true
}

function siteOf(url: string | null): string | null {
  if (!url) return null
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return null
  }
}

function isSufficiencyStatus(value: unknown): value is SufficiencyStatus {
  return value === 'short' || value === 'ok' || value === 'excess'
}

export function parseSufficiency(value: unknown, contentFill?: unknown): PlanSufficiency | null {
  const suff = isRecord(value) ? value : null
  const cf = isRecord(contentFill) ? contentFill : null
  const before = cf && isRecord(cf.before) ? cf.before : null
  const after = cf && isRecord(cf.after) ? cf.after : null

  const rawStatus = suff && isSufficiencyStatus(suff.status) ? suff.status : null
  const cfStatus =
    (after && isSufficiencyStatus(after.status) ? after.status : null) ??
    (before && isSufficiencyStatus(before.status) ? before.status : null) ??
    (cf && isSufficiencyStatus(cf.status) ? cf.status : null)

  const rawContent = suff ? firstNumber(suff, ['content_slides', 'supported_slides', 'enough_for', 'capacity']) : null
  const cfCapacity =
    (after ? firstNumber(after, ['capacity', 'content_slides']) : null) ??
    (before ? firstNumber(before, ['capacity', 'content_slides']) : null) ??
    (cf ? firstNumber(cf, ['capacity', 'content_slides']) : null)

  const rawTarget = suff ? firstNumber(suff, ['target_slides', 'target_slide_count', 'target', 'requested', 'requested_slides']) : null
  const cfRequested =
    (before ? firstNumber(before, ['requested', 'target_slides']) : null) ??
    (cf ? firstNumber(cf, ['requested_slides', 'requested']) : null) ??
    (after ? firstNumber(after, ['requested', 'target_slides']) : null) ??
    (cf ? firstNumber(cf, ['target_slides']) : null)

  const contentSlides = rawContent ?? cfCapacity
  const targetSlides = cfRequested ?? rawTarget

  if (contentSlides !== null && targetSlides !== null && contentSlides < targetSlides) {
    return { status: 'short', contentSlides, targetSlides }
  }

  const status = rawStatus ?? cfStatus
  if (!status) return null

  return {
    status,
    contentSlides,
    targetSlides: rawTarget ?? cfRequested,
  }
}

export function parseSource(value: unknown, index = 0): ResearchSource | null {
  if (!isRecord(value)) return null
  const url = firstString(value, ['url', 'href', 'link'])
  const title = firstString(value, ['title', 'name']) ?? siteOf(url)
  if (!title) return null
  return {
    id: firstString(value, ['id', 'source_id']) ?? url ?? `source-${index + 1}`,
    title,
    url,
    site: firstString(value, ['site', 'domain']) ?? siteOf(url),
    accessedAt: firstString(value, ['accessed_at', 'retrieved_at', 'fetched_at']),
    facts: firstNumber(value, ['facts_count', 'facts', 'fact_count']),
  }
}

function parseSources(value: unknown): ResearchSource[] {
  if (!Array.isArray(value)) return []
  return value.map(parseSource).filter((source): source is ResearchSource => source !== null)
}

function parseQueries(value: unknown): string[] {
  if (!Array.isArray(value)) return []
  return value
    .map((item) => (typeof item === 'string' ? item : isRecord(item) ? firstString(item, ['query', 'text']) : null))
    .filter((item): item is string => Boolean(item && item.trim()))
}

export function parseResearch(value: unknown, contentFill?: unknown): PlanResearch | null {
  const primary = isRecord(value) ? value : null
  const web = isRecord(contentFill) && isRecord(contentFill.web) ? contentFill.web : null
  if (!primary && !web) return null
  const queries = parseQueries(primary?.queries ?? web?.queries)
  const sources = parseSources(primary?.sources ?? web?.sources)
  const seconds =
    (primary ? firstNumber(primary, ['duration_s', 'seconds', 'elapsed_s']) : null) ??
    (web ? firstNumber(web, ['duration_s', 'seconds', 'elapsed_s']) : null)
  return queries.length > 0 || sources.length > 0 || seconds !== null ? { queries, sources, seconds } : null
}

function parseFactsByOrigin(value: unknown): FactsByOrigin | null {
  if (!isRecord(value)) return null
  const user = firstNumber(value, ['user', 'content', 'pack', 'source'])
  const web = firstNumber(value, ['web', 'internet', 'research', 'search'])
  const model = firstNumber(value, ['model', 'llm', 'knowledge', 'generated'])
  if (user === null && web === null && model === null) return null
  return {
    user: Math.max(0, user ?? 0),
    web: Math.max(0, web ?? 0),
    model: Math.max(0, model ?? 0),
  }
}

function resolveShortageReason(cf: Record<string, unknown> | null, root: Record<string, unknown> | null): string | null {
  const before = cf && isRecord(cf.before) ? cf.before : null
  const after = cf && isRecord(cf.after) ? cf.after : null
  const candidates = [
    cf ? firstString(cf, CONTENT_FILL_REASON_KEYS) : null,
    after ? firstString(after, CONTENT_FILL_REASON_KEYS) : null,
    before ? firstString(before, CONTENT_FILL_REASON_KEYS) : null,
    root ? firstString(root, SHORTAGE_REASON_KEYS) : null,
  ]
  for (const candidate of candidates) {
    if (isHumanSentence(candidate)) return candidate
  }
  return null
}

export function parseContentFill(value: unknown, rootPayload?: unknown): ContentFillSummary | null {
  const cf = isRecord(value) ? value : null
  const root = isRecord(rootPayload) ? rootPayload : null
  if (!cf && !root) return null

  const before = cf && isRecord(cf.before) ? cf.before : null
  const after = cf && isRecord(cf.after) ? cf.after : null
  const web = cf && isRecord(cf.web) ? cf.web : null
  const rootResearch = root && isRecord(root.research) ? root.research : null

  const capacity =
    (before ? firstNumber(before, ['capacity', 'content_slides']) : null) ??
    (after ? firstNumber(after, ['capacity', 'content_slides']) : null) ??
    (cf ? firstNumber(cf, ['capacity', 'content_slides']) : null)

  const requested =
    (before ? firstNumber(before, ['requested', 'target_slides']) : null) ??
    (cf ? firstNumber(cf, ['requested_slides', 'requested']) : null) ??
    (after ? firstNumber(after, ['requested', 'target_slides']) : null)

  const targetSlides =
    (cf ? firstNumber(cf, ['target_slides']) : null) ??
    (after ? firstNumber(after, ['requested', 'target_slides']) : null)

  const factsByOrigin = parseFactsByOrigin(cf?.facts_by_origin ?? root?.facts_by_origin)
  const webSources = parseSources(web?.sources ?? cf?.sources ?? rootResearch?.sources)
  const shortageReason = resolveShortageReason(cf, root)

  if (
    capacity === null &&
    requested === null &&
    targetSlides === null &&
    factsByOrigin === null &&
    webSources.length === 0 &&
    shortageReason === null
  ) {
    return null
  }

  return {
    capacity,
    requested,
    targetSlides,
    factsByOrigin,
    webSources,
    shortageReason,
  }
}

export function formatFactsByOriginText(contentFill: ContentFillSummary | null | undefined): string | null {
  if (!contentFill?.factsByOrigin) return null
  const { user, web } = contentFill.factsByOrigin
  const parts: string[] = []
  if (user > 0) parts.push(`из ваших материалов — ${user}`)
  if (web > 0) parts.push(`из открытых источников — ${web}`)
  if (parts.length === 0) return null
  return `Факты: ${parts.join(', ')}.`
}

export function formatShortageExplanationText(
  contentFill: ContentFillSummary | null | undefined,
  actualSlides?: number | null,
): string | null {
  if (!contentFill) return null
  if (contentFill.shortageReason) return contentFill.shortageReason
  if (contentFill.capacity !== null && contentFill.requested !== null && contentFill.capacity < contentFill.requested) {
    const built =
      typeof actualSlides === 'number' && actualSlides > 0 && actualSlides < contentFill.requested
        ? actualSlides
        : contentFill.capacity
    return `Собрал ${built} из ${contentFill.requested} запрошенных слайдов — по числу подтверждённых фактов в материалах.`
  }
  return null
}

export function unitOrigin(unit: object): UnitOrigin | null {
  const raw = (unit as Record<string, unknown>).origin
  const key = typeof raw === 'string' ? raw : isRecord(raw) ? firstString(raw, ['kind', 'type']) : null
  return key ? (ORIGIN_ALIASES[key.toLowerCase()] ?? null) : null
}

export function unitSourceId(unit: object): string | null {
  const record = unit as Record<string, unknown>
  const direct = firstString(record, ['source_id'])
  if (direct) return direct
  return isRecord(record.origin) ? firstString(record.origin, ['source_id', 'url']) : null
}

export function isUnitLocked(unit: object): boolean {
  return (unit as Record<string, unknown>).locked === true
}

function upsertSource(sources: readonly ResearchSource[], source: ResearchSource): ResearchSource[] {
  return sources.some((item) => item.id === source.id) ? sources.map((item) => (item.id === source.id ? source : item)) : [...sources, source]
}

function isSlidePlan(value: unknown): value is SlidePlan {
  return isRecord(value) && typeof value.id === 'string' && typeof value.title_intent === 'string' && Array.isArray(value.content_units)
}

export function applyPlanEvent(progress: PlanProgress, type: string, data: unknown): PlanProgress {
  if (!isRecord(data)) return progress
  const stage = firstString(data, ['stage'])
  const next = stage && stage !== progress.stage ? { ...progress, stage } : progress
  if (type === 'research.query') {
    const query = firstString(data, ['query', 'text'])
    return query && !next.queries.includes(query) ? { ...next, queries: [...next.queries, query] } : next
  }
  if (type === 'research.source') {
    const source = parseSource(data.source ?? data, next.sources.length)
    return source ? { ...next, sources: upsertSource(next.sources, source) } : next
  }
  if (type === 'plan.slide') {
    const slide = data.slide
    if (!isSlidePlan(slide)) return next
    const slides = next.slides.some((item) => item.id === slide.id) ? next.slides.map((item) => (item.id === slide.id ? slide : item)) : [...next.slides, slide]
    return { ...next, slides }
  }
  return next
}

export interface SseEvent {
  id: number | null
  type: string
  data: unknown
}

export function parseSse(text: string): SseEvent[] {
  const events: SseEvent[] = []
  for (const block of text.split(/\r?\n\r?\n/)) {
    let id: number | null = null
    let type = 'message'
    const data: string[] = []
    for (const line of block.split(/\r?\n/)) {
      const colon = line.indexOf(':')
      if (colon <= 0) continue
      const field = line.slice(0, colon)
      const value = line.slice(colon + 1).replace(/^ /, '')
      if (field === 'id') id = Number.isFinite(Number(value)) ? Number(value) : null
      else if (field === 'event') type = value
      else if (field === 'data') data.push(value)
    }
    if (data.length === 0) continue
    let parsed: unknown = data.join('\n')
    try {
      parsed = JSON.parse(parsed as string)
    } catch {
      parsed = null
    }
    events.push({ id, type, data: parsed })
  }
  return events
}
