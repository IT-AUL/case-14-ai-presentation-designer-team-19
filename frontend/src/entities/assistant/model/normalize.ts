import type { AssistantCommandResult, AssistantSession, AssistantStep, AnswersResult, BrandbookSummary, CommandChange, CommandOption, CommandStatus, Insight, IntakeKind, IntakeResult, JobEvent, JsonRecord, PackEditResult, ParsedCommand, PlanSet, QuestionsResult, RegenerateResult, SlideExplain, SlideInfo, StylePreview, StylePreviewAccepted, TemplateSummary, Understanding } from './types'

export const record = (value: unknown): JsonRecord => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonRecord : {}
export const array = (value: unknown): unknown[] => Array.isArray(value) ? value : []
export const text = (value: unknown): string | null => typeof value === 'string' ? value : null
export const number = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null
export const strings = (value: unknown): string[] => array(value).filter((item): item is string => typeof item === 'string')
export const records = (value: unknown): JsonRecord[] => array(value).filter((item): item is JsonRecord => item !== null && typeof item === 'object' && !Array.isArray(item))
const nullableRecord = (value: unknown) => value !== null && typeof value === 'object' && !Array.isArray(value) ? record(value) : null
const note = (value: JsonRecord) => text(value.assistant_note)
const isStep = (value: unknown): value is AssistantStep => typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= 7
const kind = (value: unknown): IntakeKind => value === 'template' || value === 'content' || value === 'data' || value === 'brandbook' ? value : 'unknown'

export function normalizeSession(value: unknown): AssistantSession {
  const r = record(value)
  const artifacts = record(r.artifacts)
  return {
    step: isStep(r.step) ? r.step : 1,
    completed_steps: [...new Set(array(r.completed_steps).filter(isStep))],
    skipped_steps: [...new Set(array(r.skipped_steps).filter(isStep))],
    artifacts: { template_id: text(artifacts.template_id), content_pack_id: text(artifacts.content_pack_id), brandbook_id: text(artifacts.brandbook_id), plan_id: text(artifacts.plan_id), preview_id: text(artifacts.preview_id), generation_id: text(artifacts.generation_id) },
    brief: record(r.brief), assistant_note: note(r) ?? '', job_id: text(r.job_id),
  }
}
export function normalizeIntake(value: unknown): IntakeResult {
  const r = record(value)
  return {
    project_id: text(r.project_id), job_id: text(r.job_id), template_id: text(r.template_id), content_pack_id: text(r.content_pack_id), brandbook_id: text(r.brandbook_id), assistant_note: note(r),
    files: records(r.files).filter((f) => typeof f.name === 'string').map((f) => ({ name: String(f.name), detected_kind: kind(f.detected_kind), kind: kind(f.kind), accepted: f.accepted === true, reason: text(f.reason), size_bytes: number(f.size_bytes) })),
  }
}
export function normalizeTemplateSummary(value: unknown): TemplateSummary {
  const r = record(value)
  return { template_id: text(r.template_id), filename: text(r.filename), slides: number(r.slides), roles: Object.fromEntries(Object.entries(record(r.roles)).filter((entry): entry is [string, number] => number(entry[1]) !== null)), palette: strings(r.palette), fonts: strings(r.fonts), heading_font: text(r.heading_font), body_font: text(r.body_font), preview_artifact_ids: strings(r.preview_artifact_ids), assistant_note: note(r) }
}
export function normalizeBrandbookSummary(value: unknown): BrandbookSummary {
  const r = record(value)
  return { id: text(r.id), name: text(r.name), palette: records(r.palette).map((c) => ({ ...c, name: text(c.name), hex: text(c.hex), role: text(c.role), source: text(c.source) })), fonts: records(r.fonts).map((f) => ({ ...f, role: text(f.role), family: text(f.family) })), logos: records(r.logos).map((l) => ({ ...l, file_name: text(l.file_name), artifact_id: text(l.artifact_id) })), rules: array(r.rules), sources: array(r.sources), warnings: strings(r.warnings), conflicts: records(r.conflicts).map((c) => ({ ...c, field: text(c.field), kind: text(c.kind), template: text(c.template), brandbook: text(c.brandbook), resolved: text(c.resolved), note: text(c.note) })), priority: r.priority === 'template' || r.priority === 'brandbook' ? r.priority : null, assistant_note: note(r) }
}
export function normalizeInsight(value: unknown): Insight {
  const r = record(value)
  return { id: text(r.id) ?? '', dataset_id: text(r.dataset_id) ?? '', view_id: text(r.view_id), kind: r.kind === 'recommendation' ? 'recommendation' : 'fact', fact_type: text(r.fact_type), fact: text(r.fact) ?? '', headline: text(r.headline), meaning: text(r.meaning), implication: text(r.implication), strength: number(r.strength) ?? 0, confirmed: r.confirmed === true, needs_confirmation: r.needs_confirmation === true, evidence: strings(r.evidence), numbers: strings(r.numbers) }
}
function dataPreview(value: unknown) {
  const r = nullableRecord(value)
  if (!r) return null
  return { ...r, chart_type: text(r.chart_type), categories: strings(r.categories), series: records(r.series).map((s) => ({ name: text(s.name), values: array(s.values).map(number) })), unit: text(r.unit) }
}
function chartChoice(r: JsonRecord) {
  return { ...r, type: text(r.type), reasons: strings(r.reasons) }
}
export function normalizeUnderstanding(value: unknown): Understanding {
  const r = record(value)
  const summary = record(r.summary)
  return {
    ...r, content_pack_id: text(r.content_pack_id), assistant_note: note(r) ?? note(summary),
    blocks: records(r.blocks).filter((b) => typeof b.id === 'string').map((b) => ({ ...b, id: String(b.id), kind: text(b.kind) ?? '', title: text(b.title), text: text(b.text), source_ref: nullableRecord(b.source_ref), dataset_id: text(b.dataset_id), preview: dataPreview(b.preview) })),
    summary: { capacity_slides: number(summary.capacity_slides), main_points: strings(summary.main_points), assistant_note: note(summary) },
    datasets: records(r.datasets).filter((d) => typeof d.id === 'string').map((d) => ({ ...d, id: String(d.id), title: text(d.title), file: text(d.file), profile_summary: text(d.profile_summary), task: text(d.task), task_ru: text(d.task_ru), recommendations: records(d.recommendations).map((c) => ({ ...c, chart_type: text(c.chart_type), score: number(c.score), reasons: strings(c.reasons) })), why: text(d.why), insights: records(d.insights).filter((i) => typeof i.id === 'string').map(normalizeInsight) })),
    brief: record(r.brief), context: nullableRecord(r.context), interpretation: nullableRecord(r.interpretation),
  }
}
export function normalizePackEdit(value: unknown): PackEditResult {
  const r = record(value)
  return { content_pack_id: text(r.content_pack_id), filled_from: text(r.filled_from), understanding: nullableRecord(r.understanding) ? normalizeUnderstanding(r.understanding) : null }
}
export function normalizeQuestions(value: unknown): QuestionsResult {
  const r = record(value)
  return { questions: records(r.questions).filter((q) => typeof q.id === 'string').map((q) => ({ id: String(q.id), kind: text(q.kind), reason: text(q.reason), options: records(q.options).filter((o) => typeof o.value === 'string' && typeof o.label === 'string').map((o) => ({ value: String(o.value), label: String(o.label), recommended: o.recommended === true, note: text(o.note) })), allow_custom: q.allow_custom === true, optional: q.optional === true })), suggested_brief: record(r.suggested_brief), deferred_questions: strings(r.deferred_questions), assistant_note: note(r) }
}
export function normalizeAnswers(value: unknown): AnswersResult {
  const r = record(value)
  return { brief: record(r.brief), follow_up: record(r.follow_up), assistant_note: note(r) }
}
export function normalizeStyleAccepted(value: unknown): StylePreviewAccepted {
  const r = record(value)
  return { preview_id: text(r.preview_id), job_id: text(r.job_id), events_url: text(r.events_url) }
}
export function normalizeStylePreview(value: unknown): StylePreview {
  const r = record(value)
  return { preview_id: text(r.preview_id), plan_id: text(r.plan_id), template_id: text(r.template_id), slide_ids: strings(r.slide_ids), recommended: text(r.recommended), assistant_note: note(r), seconds: number(r.seconds), styles: records(r.styles).filter((s) => typeof s.strategy === 'string').map((s) => ({ strategy: String(s.strategy), title: text(s.title), difference: text(s.difference), recommended: s.recommended === true, seconds: number(s.seconds), error: s.error ?? null, exemplars: record(s.exemplars), previews: records(s.previews).filter((p) => typeof p.slide_id === 'string').map((p) => ({ slide_id: String(p.slide_id), index: number(p.index), exemplar: text(p.exemplar), image_artifact_id: text(p.image_artifact_id) })) })) }
}
export function normalizeSlide(value: unknown): SlideInfo | null {
  const r = record(value)
  if (typeof r.id !== 'string' || typeof r.variant_id !== 'string' || number(r.index) === null) return null
  return { id: r.id, variant_id: r.variant_id, index: Number(r.index), slide_plan_id: text(r.slide_plan_id), purpose: text(r.purpose), title: text(r.title), revision: number(r.revision) ?? 1, preview_artifact_id: text(r.preview_artifact_id) }
}
export function normalizeRegenerate(value: unknown): RegenerateResult {
  const r = record(value)
  return { variant_id: text(r.variant_id), slide: normalizeSlide(r.slide), deck_revision: number(r.deck_revision), deck_artifact_id: text(r.deck_artifact_id), exemplar: text(r.exemplar), preview_artifact_id: text(r.preview_artifact_id), issues: records(r.issues), assistant_note: note(r) }
}
export function normalizeExplain(value: unknown): SlideExplain {
  const r = record(value)
  return { ...r, slide_id: text(r.slide_id), slide_plan_id: text(r.slide_plan_id), title: text(r.title), dataset: nullableRecord(r.dataset) ? { ...record(r.dataset), id: text(record(r.dataset).id), title: text(record(r.dataset).title), file: text(record(r.dataset).file), rows: number(record(r.dataset).rows) } : null, profile_summary: text(r.profile_summary), task: text(r.task), chosen: nullableRecord(r.chosen) ? chartChoice(record(r.chosen)) : null, alternatives: records(r.alternatives).map(chartChoice), rejected: records(r.rejected).map(chartChoice), insights: array(r.insights), assistant_note: note(r) }
}
export function normalizeJobEvent(value: unknown, eventType?: string, id?: string): JobEvent | null {
  const r = record(value)
  const sequence = number(r.sequence) ?? (id && /^\d+$/.test(id) ? Number(id) : null)
  const type = text(r.type) ?? text(r.event) ?? eventType
  if (sequence === null || !Number.isSafeInteger(sequence) || sequence < 0 || !type) return null
  return { ...r, sequence, type, timestamp: text(r.timestamp), stage: text(r.stage), progress: number(r.progress), message: text(r.message), data: nullableRecord(r.data) ?? r }
}
function normalizeCommandOptions(value: unknown): CommandOption[] {
  return array(value).flatMap((item): CommandOption[] => {
    if (typeof item === 'string' && item.trim()) return [{ value: item.trim(), label: item.trim() }]
    const r = nullableRecord(item)
    if (!r) return []
    const val = text(r.value) ?? text(r.command) ?? text(r.text) ?? text(r.label)
    const label = text(r.label) ?? text(r.title) ?? val
    return val && label ? [{ value: val, label }] : []
  })
}
function normalizeParsedCommand(value: unknown): ParsedCommand | null {
  const r = nullableRecord(value)
  if (!r) return null
  return {
    ...r,
    intent: text(r.intent),
    action: text(r.action),
    text: text(r.text) ?? '',
    chart_type: text(r.chart_type),
    brief: records(r.brief).filter((b) => typeof b.field === 'string').map((b) => ({ field: String(b.field), value: b.value, label: text(b.label) })),
    question: text(r.question),
  }
}
function normalizeCommandChange(value: unknown): CommandChange | null {
  const r = nullableRecord(value)
  if (!r) return null
  return {
    ...r,
    id: text(r.id),
    job_id: text(r.job_id),
    variant_id: text(r.variant_id),
    slide_id: text(r.slide_id),
    expected_revision: number(r.expected_revision),
    target_revision: number(r.target_revision),
    action: text(r.action),
    state: text(r.state),
    events_url: text(r.events_url),
  }
}
export function normalizeCommandResult(value: unknown): AssistantCommandResult {
  const r = record(value)
  const rawStatus = text(r.status)
  const status: CommandStatus = rawStatus === 'applied' || rawStatus === 'accepted' || rawStatus === 'clarification' ? rawStatus : nullableRecord(r.change) ? 'accepted' : 'clarification'
  const planRec = nullableRecord(r.plan)
  const plan = planRec && typeof planRec.plan_id === 'string' && Array.isArray(planRec.plans) ? (planRec as unknown as PlanSet) : null
  const cmdRec = nullableRecord(r.command)
  const options = normalizeCommandOptions(r.options ?? r.choices ?? cmdRec?.options ?? cmdRec?.choices)
  return {
    ...r,
    status,
    parser: text(r.parser),
    command: normalizeParsedCommand(r.command),
    assistant_note: note(r),
    brief: nullableRecord(r.brief),
    context_revision: number(r.context_revision),
    changed_fields: strings(r.changed_fields),
    invalidated: strings(r.invalidated),
    change: normalizeCommandChange(r.change),
    plan,
    options,
  }
}

