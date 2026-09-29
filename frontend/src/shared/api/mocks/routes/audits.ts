import type { RequestHandler } from 'msw'
import { applyRepair, dismissIssue, filterIssues, runContextualAudit } from '../audit'
import { page } from '../context'
import type { MockContext } from '../context'
import { createDeckArtifact } from '../deliverables'
import { json, stateConflict } from '../http'
import type { MockStore, VariantRecord } from '../store'
import { optionalInteger, optionalString, readJson, requiredString, stringArray } from '../validate'

function projectOf(store: MockStore, variant: VariantRecord): string | null {
  return store.runs.get(variant.summary.run_id)?.detail.project_id ?? null
}

export function auditRoutes({ store, router }: MockContext): RequestHandler[] {
  const { route } = router
  return [
    route('post', '/variants/:variantId/audits', async ({ request, param }) => {
      const variant = store.variant(param('variantId'))
      const body = await readJson(request)
      if (!variant.auditId) throw stateConflict(`Variant ${variant.summary.id} is not rendered yet`, { status: variant.summary.status })
      const record = store.audit(variant.auditId)
      const providerSessionId = optionalString(body, 'provider_session_id')
      if (providerSessionId) runContextualAudit(store, record, variant, store.session(providerSessionId))
      const configVersion = optionalString(body, 'config_version')
      if (configVersion) record.run = { ...record.run, config_version: configVersion }
      const job = store.createJob('audit', projectOf(store, variant), { resultIds: { audit_id: record.run.id } })
      return json({ audit_id: record.run.id, job_id: job.id }, 202)
    }),

    route('get', '/audits/:auditId', ({ param }) => json(store.audit(param('auditId')).run)),

    route('get', '/audits/:auditId/issues', ({ param, url }) => {
      const record = store.audit(param('auditId'))
      return json(page(filterIssues(store, record, url)))
    }),

    route('post', '/audits/:auditId/repairs', async ({ request, param }) => {
      const record = store.audit(param('auditId'))
      const body = await readJson(request)
      const selected = stringArray(body, 'selected_issue_ids', { min: 1, max: 1000 })
      optionalInteger(body, 'max_iterations', { min: 1, max: 5, fallback: 2 })
      const providerSessionId = optionalString(body, 'provider_session_id')
      if (providerSessionId) store.session(providerSessionId)
      const variant = store.variant(record.run.variant_id)
      const outcome = applyRepair(store, record, variant, selected)
      const deck = await createDeckArtifact(store, variant)
      variant.summary = { ...variant.summary, deck_artifact_id: deck.id }
      const job = store.createJob('repair', projectOf(store, variant), {
        resultIds: {
          audit_id: record.run.id,
          deck_revision: String(outcome.revision),
          deck_artifact_id: deck.id,
          applied: String(outcome.applied),
          skipped: String(outcome.skipped),
          failed: '0',
          not_implemented: '0',
          unresolved: String(outcome.unresolved),
        },
      })
      return json({ job_id: job.id, audit_id: record.run.id, deck_revision: outcome.revision }, 202)
    }),

    route('post', '/issues/:issueId/dismiss', async ({ request, param }) => {
      const body = await readJson(request)
      requiredString(body, 'reason', 2000)
      return json(dismissIssue(store, param('issueId')))
    }),
  ]
}
