import { isActiveIssue } from './audit'
import { passportFixture } from './fixtures'
import type { QualityPassport } from './fixtures'
import { nowIso, sha256Hex } from './ids'
import type { ArtifactRecord, MockStore, VariantRecord } from './store'

export async function buildPassport(store: MockStore, variant: VariantRecord, deck: ArtifactRecord): Promise<QualityPassport> {
  const run = store.run(variant.summary.run_id)
  const template = store.templates.get(run.detail.template_id)?.asset
  const issues = variant.auditId ? store.audit(variant.auditId).issues : []
  const active = issues.filter(isActiveIssue)
  const count = (predicate: (issue: (typeof issues)[number]) => boolean) => active.filter(predicate).length
  const finishedAt = run.detail.finished_at ?? nowIso()
  const totalSeconds = Math.max(0, (Date.parse(finishedAt) - Date.parse(run.detail.created_at)) / 1000)
  const base = passportFixture

  return {
    ...base,
    run_id: variant.auditId ?? run.detail.id,
    generated_at: nowIso(),
    inputs: {
      template_sha256: template?.sha256 ?? base.inputs.template_sha256,
      template_name: template?.filename ?? base.inputs.template_name,
      content_pack_id: run.detail.content_pack_id,
      brief_hash: await sha256Hex(JSON.stringify(run.request.brief)),
    },
    metrics: {
      ...base.metrics,
      readability: {
        contrast_failures: count((issue) => issue.rule_code === 'accessibility.contrast'),
        overflow_count: count((issue) => issue.rule_code === 'text.overflow'),
      },
      timings: { total_seconds: totalSeconds },
    },
    issues_summary: {
      blocker: count((issue) => issue.severity === 'blocker'),
      error: count((issue) => issue.severity === 'error'),
      warning: count((issue) => issue.severity === 'warning'),
      info: count((issue) => issue.severity === 'info'),
      unresolved: active.length,
    },
    exports: [{ format: 'pptx', artifact_id: deck.id, sha256: deck.sha256, size_bytes: deck.sizeBytes }],
  }
}
