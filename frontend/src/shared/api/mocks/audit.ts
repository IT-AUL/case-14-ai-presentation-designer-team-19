import { notFound, stateConflict, validationError } from './http'
import { nowIso } from './ids'
import type { AuditRecord, MockStore, VariantRecord } from './store'
import type { AuditIssue, ProviderSession, Severity } from './types'
import { queryBoolean, queryInteger, queryList } from './validate'

export const REPAIR_HANDLERS: ReadonlySet<string> = new Set([
  'layout.out_of_bounds',
  'layout.edge_margin',
  'image.aspect_ratio',
  'text.overflow',
  'text.slide_clip',
  'accessibility.contrast',
  'template.font_family',
  'template.color_palette',
  'template.anchor_position',
  'integrity.empty_slide',
  'integrity.duplicate_slide',
  'integrity.placeholder_text',
  'editability.raster_only',
])

const SEVERITIES: readonly Severity[] = ['info', 'warning', 'error', 'blocker']
const STATUSES: readonly AuditIssue['status'][] = ['open', 'selected', 'fixed', 'dismissed', 'unresolved']
const ACTIVE_STATUSES: ReadonlySet<AuditIssue['status']> = new Set(['open', 'selected', 'unresolved'])

export function isActiveIssue(issue: AuditIssue): boolean {
  return ACTIVE_STATUSES.has(issue.status)
}
const CONTEXTUAL_SKIPPED_PURPOSES = new Set(['title', 'agenda', 'section_divider', 'qa', 'thank_you', 'cta'])
const CONTEXTUAL_PROMPT_VERSION = 'vlm-audit/0.1.0'

export function recount(store: MockStore, record: AuditRecord): void {
  const active = record.issues.filter(isActiveIssue)
  const bySeverity = { info: 0, warning: 0, error: 0, blocker: 0 }
  const byType: Record<string, number> = {}
  for (const issue of active) {
    bySeverity[issue.severity] += 1
    byType[issue.rule_code] = (byType[issue.rule_code] ?? 0) + 1
  }
  record.run = { ...record.run, issue_count: active.length, summary_by_severity: bySeverity, summary_by_type: byType }
  const variant = store.variants.get(record.run.variant_id)
  if (variant) {
    variant.summary = {
      ...variant.summary,
      metrics: { ...variant.summary.metrics, issues_total: active.length },
      contextual_issues: record.run.contextual_status === 'completed' ? active.filter((issue) => !issue.deterministic).length : variant.summary.contextual_issues,
    }
  }
}

export function filterIssues(store: MockStore, record: AuditRecord, url: URL): AuditIssue[] {
  const limit = queryInteger(url, 'limit', { min: 1, max: 1000, fallback: 100 })
  const slideId = url.searchParams.get('slide_id')
  const ruleCodes = queryList(url, 'rule_code')
  const severities = queryList(url, 'severity')
  const statuses = queryList(url, 'status')
  const deterministic = queryBoolean(url, 'deterministic')
  const repairable = queryBoolean(url, 'repairable')

  if (severities?.some((value) => !SEVERITIES.some((severity) => severity === value))) {
    throw validationError('severity', `Query parameter 'severity' must be one of ${SEVERITIES.join(', ')}`, 'query')
  }
  if (statuses?.some((value) => !STATUSES.some((status) => status === value))) {
    throw validationError('status', `Query parameter 'status' must be one of ${STATUSES.join(', ')}`, 'query')
  }

  const slide = slideId ? store.slides.get(slideId) : undefined
  const slideIndex = slide?.variant_id === record.run.variant_id ? slide.index : undefined
  return record.issues
    .filter((issue) => {
      if (slideId && issue.slide_id !== slideId && (slideIndex === undefined || issue.slide_index !== slideIndex)) return false
      if (ruleCodes && !ruleCodes.includes(issue.rule_code)) return false
      if (severities && !severities.includes(issue.severity)) return false
      if (statuses && !statuses.includes(issue.status)) return false
      if (deterministic !== null && issue.deterministic !== deterministic) return false
      if (repairable !== null && issue.repairable !== repairable) return false
      return true
    })
    .slice(0, limit)
}

export interface RepairOutcome {
  revision: number
  applied: number
  skipped: number
  unresolved: number
}

export function applyRepair(store: MockStore, record: AuditRecord, variant: VariantRecord, selectedIds: readonly string[]): RepairOutcome {
  const known = new Set(record.issues.map((issue) => issue.id))
  const unknown = selectedIds.filter((id) => !known.has(id))
  if (unknown.length > 0) {
    throw validationError('selected_issue_ids', `Issues do not belong to audit ${record.run.id}: ${unknown.join(', ')}`)
  }

  const selected = new Set(selectedIds)
  const revision = variant.revision + 1
  const outcome: RepairOutcome = { revision, applied: 0, skipped: 0, unresolved: 0 }
  record.issues = record.issues.map((issue) => {
    const next = { ...issue, deck_revision: revision }
    if (!selected.has(issue.id)) return next
    if (issue.status === 'fixed' || issue.status === 'dismissed') {
      outcome.skipped += 1
      return next
    }
    if (issue.repairable && REPAIR_HANDLERS.has(issue.rule_code)) {
      outcome.applied += 1
      return { ...next, status: 'fixed' }
    }
    outcome.unresolved += 1
    return { ...next, status: 'unresolved' }
  })

  variant.revision = revision
  variant.slides = variant.slides.map((slide) => ({ ...slide, revision }))
  for (const slide of variant.slides) store.slides.set(slide.id, slide)
  record.run = { ...record.run, deck_revision: revision, finished_at: nowIso() }
  recount(store, record)
  return outcome
}

export function dismissIssue(store: MockStore, issueId: string): AuditIssue {
  const auditId = store.issueAudits.get(issueId)
  const record = auditId ? store.audits.get(auditId) : undefined
  const issue = record?.issues.find((candidate) => candidate.id === issueId)
  if (!record || !issue) throw notFound('Issue', issueId)
  if (issue.status === 'fixed') throw stateConflict(`Issue ${issueId} is already fixed`, { status: issue.status })
  const dismissed: AuditIssue = { ...issue, status: 'dismissed' }
  record.issues = record.issues.map((candidate) => (candidate.id === issueId ? dismissed : candidate))
  recount(store, record)
  return dismissed
}

export function runContextualAudit(store: MockStore, record: AuditRecord, variant: VariantRecord, session: ProviderSession): void {
  if (record.run.contextual_status === 'completed') return
  const created: AuditIssue[] = variant.slides
    .filter((slide) => !CONTEXTUAL_SKIPPED_PURPOSES.has(slide.purpose ?? ''))
    .filter((slide) => (slide.title ?? '').trim().split(/\s+/).length <= 3)
    .map((slide, position) => ({
      schema_version: '1.0',
      id: `${record.run.id}:content.conclusion_title:s${slide.index}:${position + 1}`,
      audit_run_id: record.run.id,
      deck_revision: variant.revision,
      rule_code: 'content.conclusion_title',
      deterministic: false,
      severity: 'warning',
      slide_id: slide.id,
      slide_index: slide.index,
      shape_ids: [],
      bbox: { x: 0.05, y: 0.05, w: 0.9, h: 0.16 },
      message: `Заголовок «${slide.title ?? ''}» называет тему, а не вывод слайда`,
      measured_value: null,
      threshold: null,
      evidence: [{ kind: 'model_verdict', ref: `slide[${slide.index}]`, detail: 'Заголовок не формулирует главный тезис слайда' }],
      confidence: 0.74,
      status: 'open',
      repairable: false,
      proposed_actions: ['rewrite_title'],
      provenance: { rule_version: null, model_id: session.models.vision, prompt_version: CONTEXTUAL_PROMPT_VERSION },
    }))
  record.issues = [...record.issues, ...created]
  for (const issue of created) store.issueAudits.set(issue.id, record.run.id)
  record.run = { ...record.run, contextual_status: 'completed', finished_at: nowIso() }
  recount(store, record)
}
