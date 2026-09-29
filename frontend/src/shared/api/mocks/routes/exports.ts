import { HttpResponse } from 'msw'
import type { RequestHandler } from 'msw'
import type { MockContext } from '../context'
import { createExport, parseFormats } from '../deliverables'
import { json, MockApiError, stateConflict } from '../http'
import type { ArtifactRecord } from '../store'
import { readJson } from '../validate'

function contentDisposition(filename: string): string {
  const fallback = filename.replace(/[^\x20-\x7e]/g, '_').replace(/["\\]/g, '_')
  return `attachment; filename="${fallback}"; filename*=UTF-8''${encodeURIComponent(filename)}`
}

export function exportRoutes({ store, router, loadPdf }: MockContext): RequestHandler[] {
  const { route } = router

  const body = async (artifact: ArtifactRecord): Promise<ArrayBuffer | Uint8Array<ArrayBuffer>> => {
    if (artifact.body.kind === 'bytes') return artifact.body.bytes
    try {
      return await loadPdf(artifact.body.strategy)
    } catch {
      throw new MockApiError(503, 'artifact_unavailable', `Artifact ${artifact.id} could not be loaded`, { stage: 'export', retryable: true })
    }
  }

  return [
    route('post', '/variants/:variantId/exports', async ({ request, param }) => {
      const variant = store.variant(param('variantId'))
      const formats = parseFormats((await readJson(request)).formats)
      if (variant.summary.status !== 'completed') {
        throw stateConflict(`Variant ${variant.summary.id} is ${variant.summary.status}`, { status: variant.summary.status })
      }
      const projectId = store.runs.get(variant.summary.run_id)?.detail.project_id ?? null
      const job = store.createJob('export', projectId)
      const record = await createExport(store, variant, formats, { jobId: job.id })
      store.updateJob(job.id, { result_ids: { export_id: record.id } })
      return json({ export_id: record.id, job_id: job.id }, 202)
    }),

    route('get', '/exports/:exportId', ({ param }) => json(store.exportRecord(param('exportId')))),

    route('get', '/artifacts/:artifactId/download', async ({ param }) => {
      const artifact = store.artifact(param('artifactId'))
      const bytes = await body(artifact)
      return new HttpResponse(bytes, {
        headers: {
          'Content-Type': artifact.mimeType,
          'Content-Disposition': contentDisposition(artifact.filename),
          'Content-Length': String(bytes.byteLength),
        },
      })
    }),
  ]
}
