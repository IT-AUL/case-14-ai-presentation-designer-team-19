import type { RequestHandler } from 'msw'
import { page } from '../context'
import type { MockContext } from '../context'
import { designDnaFixture, templateDetailFixture } from '../fixtures'
import { json, MockApiError, notFound, validationError } from '../http'
import { nowIso, prefixedId, rebrand, sha256Hex } from '../ids'
import { PPTX_MIME } from '../pptx'
import type { TemplateRecord } from '../store'
import type { SlidePreviewMeta, TemplateAnalysis, TemplateAsset, TemplateDetail, UploadedFile } from '../types'
import { formFiles, optionalString, queryInteger, readForm, readJson } from '../validate'

const MAX_TEMPLATE_BYTES = 200 * 1024 * 1024
const POTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.template'

function packageType(file: UploadedFile): TemplateAsset['package_type'] {
  const extension = file.name.toLowerCase().split('.').pop()
  if (extension === 'pptx' || extension === 'potx') return extension
  throw new MockApiError(415, 'unsupported_media_type', 'Template must be a .pptx or .potx file', { stage: 'ingest', details: { filename: file.name } })
}

function assertPackage(file: UploadedFile): void {
  if (file.bytes.byteLength > MAX_TEMPLATE_BYTES) {
    throw new MockApiError(413, 'payload_too_large', 'Template exceeds the 200 MB limit', { stage: 'ingest', details: { size_bytes: file.bytes.byteLength } })
  }
  if (file.bytes[0] !== 0x50 || file.bytes[1] !== 0x4b) {
    throw new MockApiError(422, 'invalid_template', 'Template is not a valid OOXML package', { stage: 'ingest', details: { filename: file.name } })
  }
}

function detail(record: TemplateRecord): TemplateDetail {
  return { ...record.asset, latest_analysis: record.analysis }
}

function analyze(record: TemplateRecord, configVersion: string | null): TemplateAnalysis {
  const source = templateDetailFixture.latest_analysis
  if (!source) throw new MockApiError(500, 'internal_error', 'Recorded template analysis is missing', { retryable: false })
  const createdAt = nowIso()
  const revision = (record.analysis?.revision ?? 0) + 1
  const analysis: TemplateAnalysis = {
    ...source,
    id: prefixedId('ana'),
    template_id: record.asset.id,
    revision,
    design_dna_revision: revision,
    config_version: configVersion,
    package_inventory: { ...source.package_inventory, path: record.asset.filename },
    created_at: createdAt,
    finished_at: createdAt,
  }
  const replacements = new Map([
    [designDnaFixture.template_id, record.asset.id],
    [designDnaFixture.analysis_id, analysis.id],
  ])
  record.analysis = analysis
  record.dna = { ...rebrand(designDnaFixture, replacements), created_at: createdAt }
  return analysis
}

function slidePreviews(record: TemplateRecord): SlidePreviewMeta[] {
  return (record.dna?.exemplars ?? []).map((exemplar) => ({
    id: `${record.asset.id}:s${exemplar.slide_index}`,
    slide_index: exemplar.slide_index,
    role: exemplar.role,
    purpose: null,
    title: null,
    part: exemplar.part,
    mutability: exemplar.mutability ?? null,
    preview_artifact_id: exemplar.preview_artifact_id ?? null,
  }))
}

export function templateRoutes({ store, router }: MockContext): RequestHandler[] {
  const { route } = router
  return [
    route('post', '/projects/:projectId/templates', async ({ request, param }) => {
      const project = store.project(param('projectId'))
      const [file] = await formFiles(await readForm(request), 'file')
      if (!file) throw validationError('file', "Field 'file' is required")
      const type = packageType(file)
      assertPackage(file)
      const artifact = await store.storeBytes({ filename: file.name, mimeType: type === 'potx' ? POTX_MIME : PPTX_MIME, bytes: file.bytes })
      const asset: TemplateAsset = {
        id: prefixedId('tpl'),
        project_id: project.id,
        filename: file.name,
        media_type: artifact.mimeType,
        package_type: type,
        sha256: await sha256Hex(file.bytes),
        size_bytes: file.bytes.byteLength,
        artifact_id: artifact.id,
        validation_status: 'valid',
        created_at: nowIso(),
      }
      store.templates.set(asset.id, { asset, analysis: null, dna: null })
      store.touchProject(project.id, { template_id: asset.id })
      return json(asset, 201)
    }),

    route('post', '/templates/:templateId/analyze', async ({ request, param }) => {
      const record = store.template(param('templateId'))
      const body = await readJson(request)
      const providerSessionId = optionalString(body, 'provider_session_id')
      if (providerSessionId) store.session(providerSessionId)
      const analysis = analyze(record, optionalString(body, 'config_version'))
      const job = store.createJob('template_analysis', record.asset.project_id, {
        resultIds: { analysis_id: analysis.id, design_dna_revision: String(analysis.revision) },
      })
      return json({ job_id: job.id, analysis_id: analysis.id }, 202)
    }),

    route('get', '/templates/:templateId', ({ param }) => json(detail(store.template(param('templateId'))))),

    route('get', '/templates/:templateId/design-dna', ({ param }) => {
      const record = store.template(param('templateId'))
      if (!record.dna) throw notFound('Design DNA for template', record.asset.id)
      return json(record.dna)
    }),

    route('get', '/templates/:templateId/slides', ({ param, url }) => {
      const record = store.template(param('templateId'))
      const limit = queryInteger(url, 'limit', { min: 1, max: 500, fallback: 100 })
      return json(page(slidePreviews(record).slice(0, limit)))
    }),
  ]
}
