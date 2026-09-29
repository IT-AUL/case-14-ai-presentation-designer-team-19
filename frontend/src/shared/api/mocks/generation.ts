import { API_BASE } from '@/shared/config'
import { recount } from './audit'
import { createDeckArtifact, createExport } from './deliverables'
import { generationFixture, variantFixtures } from './fixtures'
import type { VariantFixture } from './fixtures'
import { stateConflict, validationError } from './http'
import { nowIso, prefixedId, randomHex, rebrand } from './ids'
import type { MockStore, RunRecord, VariantRecord } from './store'
import type { Brief, ExportFormat, FixtureStrategy, GenerationCreate, GenerationDetail, Job, VariantStrategy } from './types'
import { isRecord, optionalBoolean, optionalInteger, optionalString, requiredString } from './validate'
import type { JsonObject } from './validate'

const STRATEGIES: readonly VariantStrategy[] = ['faithful', 'balanced', 'visual', 'custom']
const FIXTURE_PACK_ID = generationFixture.content_pack_id
const FIXTURE_PLAN_PREFIX = `plan-${FIXTURE_PACK_ID.replace(/^pack-/, '')}`
const TERMINAL_STATES: ReadonlySet<Job['state']> = new Set(['completed', 'failed', 'canceled'])

export interface VariantIdentity {
  auditId: string
  slideIds: ReadonlyMap<string, string>
  deckArtifactId?: string
  exportId?: string
  artifactIds?: Partial<Record<ExportFormat, string>>
}

export function fixtureFor(strategy: string): FixtureStrategy {
  return strategy === 'faithful' || strategy === 'visual' ? strategy : 'balanced'
}

export function freshIdentity(fixture: VariantFixture): VariantIdentity {
  return { auditId: randomHex(), slideIds: new Map(fixture.slides.map((slide) => [slide.id, prefixedId('sld')])) }
}

function stringList(body: JsonObject, field: string): string[] | null {
  const value = body[field]
  if (value === undefined || value === null) return null
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw validationError(`brief.${field}`, `Field 'brief.${field}' must be an array of strings`)
  }
  return value as string[]
}

function parseBrief(value: unknown): Brief {
  if (!isRecord(value)) throw validationError('brief', "Field 'brief' is required")
  return {
    purpose: requiredString(value, 'purpose'),
    audience: requiredString(value, 'audience'),
    language: requiredString(value, 'language', 16),
    target_slide_count: optionalInteger(value, 'target_slide_count', { min: 3, max: 40, fallback: 12 }),
    tone: optionalString(value, 'tone'),
    mandatory_sections: stringList(value, 'mandatory_sections'),
    forbidden_claims: stringList(value, 'forbidden_claims'),
  }
}

function parseVariants(value: unknown): GenerationCreate['variants'] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 3) {
    throw validationError('variants', "Field 'variants' must contain between 1 and 3 items")
  }
  return value.map((item) => {
    const strategy = isRecord(item) ? STRATEGIES.find((candidate) => candidate === item.strategy) : undefined
    if (!strategy) throw validationError('variants', `Each variant needs a strategy: ${STRATEGIES.join(', ')}`)
    return { strategy }
  })
}

export function parseGenerationRequest(store: MockStore, projectId: string, body: JsonObject): GenerationCreate {
  store.project(projectId)
  const templateId = requiredString(body, 'template_id')
  const template = store.template(templateId)
  if (!template.analysis || !template.dna) throw stateConflict(`Template ${templateId} has not been analyzed yet`, { template_id: templateId })
  const contentPackId = requiredString(body, 'content_pack_id')
  store.contentPack(contentPackId)
  const providerSessionId = optionalString(body, 'provider_session_id')
  if (providerSessionId) store.session(providerSessionId)
  return {
    template_id: templateId,
    content_pack_id: contentPackId,
    provider_session_id: providerSessionId,
    brief: parseBrief(body.brief),
    variants: parseVariants(body.variants),
    config_version: optionalString(body, 'config_version'),
    seed: null,
    use_llm: optionalBoolean(body, 'use_llm', false),
  }
}

function planKey(contentPackId: string): string {
  return `plan-${contentPackId.replace(/^pack-/, '')}`
}

export function startGeneration(store: MockStore, projectId: string, request: GenerationCreate, parentRunId: string | null = null): RunRecord {
  const createdAt = nowIso()
  const job = store.createJob('generation', projectId, { state: 'running', startedAt: createdAt })
  store.updateJob(job.id, { stage: 'planning', progress: 0.05 })
  const runId = prefixedId('run')
  const variantIds = request.variants.map(({ strategy }) => {
    const record: VariantRecord = {
      summary: {
        id: prefixedId('var'),
        run_id: runId,
        strategy,
        status: 'queued',
        rationale: null,
        deck_artifact_id: null,
        montage_artifact_id: null,
        metrics: null,
        audit_status: 'not_started',
        export_ids: [],
        planner: null,
        contextual_issues: null,
        deck_plan_id: `${planKey(request.content_pack_id)}-${strategy}`,
      },
      fixture: fixtureFor(strategy),
      slides: [],
      auditId: null,
      revision: 1,
    }
    store.variants.set(record.summary.id, record)
    return record.summary.id
  })
  const run: RunRecord = {
    request,
    variantIds,
    detail: {
      id: runId,
      project_id: projectId,
      state: 'running',
      job_id: job.id,
      events_url: `${API_BASE}/jobs/${job.id}/events`,
      template_id: request.template_id,
      content_pack_id: request.content_pack_id,
      deck_plan_id: null,
      deck_plan: null,
      parent_run_id: parentRunId,
      created_at: createdAt,
      finished_at: null,
    },
  }
  store.runs.set(runId, run)
  return run
}

export async function materializeVariant(store: MockStore, run: RunRecord, variant: VariantRecord, identity: VariantIdentity): Promise<void> {
  const fixture = variantFixtures[variant.fixture]
  const finishedAt = run.detail.finished_at ?? nowIso()
  const replacements = new Map<string, string>([
    [fixture.variant.id, variant.summary.id],
    [fixture.variant.run_id, run.detail.id],
    [fixture.audit.id, identity.auditId],
    ...identity.slideIds,
  ])
  const data = rebrand({ slides: fixture.slides, audit: fixture.audit, issues: fixture.issues }, replacements)

  variant.slides = data.slides
  for (const slide of data.slides) store.slides.set(slide.id, slide)
  const audit = {
    run: { ...data.audit, deck_revision: variant.revision, created_at: run.detail.created_at, finished_at: finishedAt },
    issues: data.issues,
  }
  store.indexAudit(audit)
  variant.auditId = identity.auditId
  variant.summary = {
    ...variant.summary,
    status: 'completed',
    metrics: { ...fixture.variant.metrics },
    audit_status: 'completed',
    planner: fixture.variant.planner,
  }
  const deck = await createDeckArtifact(store, variant, identity.deckArtifactId)
  variant.summary = { ...variant.summary, deck_artifact_id: deck.id }
  recount(store, audit)
  await createExport(store, variant, ['pdf', 'quality_passport'], {
    exportId: identity.exportId,
    jobId: run.detail.job_id,
    artifactIds: identity.artifactIds,
    createdAt: run.detail.created_at,
  })
}

function buildDeckPlan(run: RunRecord, variant: VariantRecord | undefined): GenerationDetail['deck_plan'] {
  const source = generationFixture.deck_plan
  if (!source) return null
  const replacements = new Map([
    [FIXTURE_PACK_ID, run.detail.content_pack_id],
    [FIXTURE_PLAN_PREFIX, planKey(run.detail.content_pack_id)],
  ])
  const plan = rebrand(source, replacements)
  return { ...plan, id: variant?.summary.deck_plan_id ?? plan.id, brief: run.request.brief, audience: run.request.brief.audience, language: run.request.brief.language }
}

export async function completeGeneration(store: MockStore, runId: string, finishedAt = nowIso(), identities?: ReadonlyMap<string, VariantIdentity>): Promise<void> {
  const run = store.run(runId)
  if (run.detail.state !== 'running') return
  run.detail = { ...run.detail, finished_at: finishedAt }
  const variants = run.variantIds.map((id) => store.variant(id))
  for (const variant of variants) {
    const identity = identities?.get(variant.summary.id) ?? freshIdentity(variantFixtures[variant.fixture])
    await materializeVariant(store, run, variant, identity)
  }
  const plan = buildDeckPlan(run, variants[0])
  run.detail = { ...run.detail, state: 'completed', deck_plan: plan, deck_plan_id: plan?.id ?? null }
  store.updateJob(run.detail.job_id, {
    state: 'completed',
    stage: null,
    progress: 1,
    finished_at: finishedAt,
    result_ids: plan ? { deck_plan_id: plan.id } : {},
  })
}

export function cancelGeneration(store: MockStore, runId: string): Job {
  const run = store.run(runId)
  if (TERMINAL_STATES.has(run.detail.state)) {
    throw stateConflict(`Generation ${runId} is already ${run.detail.state}`, { state: run.detail.state })
  }
  const finishedAt = nowIso()
  run.detail = { ...run.detail, state: 'canceled', finished_at: finishedAt }
  for (const id of run.variantIds) {
    const variant = store.variant(id)
    variant.summary = { ...variant.summary, status: 'canceled' }
  }
  return store.updateJob(run.detail.job_id, { state: 'canceled', finished_at: finishedAt })
}

export function assertRetryable(store: MockStore, runId: string): RunRecord {
  const run = store.run(runId)
  if (!TERMINAL_STATES.has(run.detail.state)) {
    throw stateConflict(`Generation ${runId} is still ${run.detail.state}`, { state: run.detail.state })
  }
  return run
}

export function generationDetail(store: MockStore, run: RunRecord): GenerationDetail {
  return { ...run.detail, variants: run.variantIds.map((id) => store.variant(id).summary) }
}
