import { setupServer } from 'msw/node'
import type { components, paths } from '../schema'
import { projectFixture, variantFixtures } from './fixtures'
import { createMockBackend } from './handlers'
import { createPlaceholderPptx, PPTX_MIME } from './pptx'

type ErrorEnvelope = components['schemas']['ErrorEnvelope']
type IssueQuery = NonNullable<paths['/api/v1/audits/{audit_id}/issues']['get']['parameters']['query']>

interface Part {
  name: string
  content: string | Uint8Array<ArrayBuffer>
  filename?: string
  type?: string
}

const PDF_BYTES = new TextEncoder().encode('%PDF-1.7\n%mock\n')
const backend = createMockBackend({ latency: 0, generationDelay: 0, loadPdf: async () => PDF_BYTES.buffer })
const server = setupServer(...backend.handlers)

async function loadClient() {
  const NativeRequest = globalThis.Request
  class DocumentRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === 'string' ? new URL(input, window.location.origin) : input, init)
    }
  }
  vi.stubGlobal('Request', DocumentRequest)
  try {
    return await import('@/shared/api')
  } finally {
    vi.unstubAllGlobals()
  }
}

let client: Awaited<ReturnType<typeof loadClient>>

function multipart(parts: readonly Part[]): { body: Uint8Array<ArrayBuffer>; contentType: string } {
  const encoder = new TextEncoder()
  const boundary = `----deckdna${Date.now().toString(16)}`
  const chunks: Uint8Array[] = []
  for (const part of parts) {
    const disposition = `form-data; name="${part.name}"${part.filename ? `; filename="${part.filename}"` : ''}`
    const type = part.type ? `Content-Type: ${part.type}\r\n` : ''
    chunks.push(encoder.encode(`--${boundary}\r\nContent-Disposition: ${disposition}\r\n${type}\r\n`))
    chunks.push(typeof part.content === 'string' ? encoder.encode(part.content) : part.content)
    chunks.push(encoder.encode('\r\n'))
  }
  chunks.push(encoder.encode(`--${boundary}--\r\n`))
  const body = new Uint8Array(chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0))
  let offset = 0
  for (const chunk of chunks) {
    body.set(chunk, offset)
    offset += chunk.byteLength
  }
  return { body, contentType: `multipart/form-data; boundary=${boundary}` }
}

async function expectApiError(request: Promise<unknown>, status: number, code: string) {
  const error = await request.then(
    () => null,
    (reason: unknown) => reason,
  )
  expect(client.isApiError(error)).toBe(true)
  expect(error).toMatchObject({ status, code })
}

beforeAll(async () => {
  server.listen({ onUnhandledRequest: 'error' })
  client = await loadClient()
})

afterAll(() => server.close())

describe('mock backend', () => {
  it('seeds the recorded project and reports html export as unavailable', async () => {
    const { api, unwrap } = client
    const projects = await unwrap(api.GET('/api/v1/projects'))
    expect(projects.next_cursor).toBeNull()
    expect(projects.items.map((project) => project.id)).toContain(projectFixture.id)

    const capabilities = await unwrap(api.GET('/api/v1/capabilities'))
    expect(capabilities.exports).toMatchObject({ pdf: true, pptx: true, html: false })
  })

  it('drives the full demo path through the typed client', async () => {
    const { api, unwrap } = client

    const project = await unwrap(api.POST('/api/v1/projects', { body: { name: 'Запуск продукта', default_language: 'ru', target_slide_count: 12 } }))
    expect(project.status).toBe('draft')

    const templateBytes = createPlaceholderPptx([{ title: 'Шаблон', lines: [] }])
    const templateForm = multipart([{ name: 'file', filename: 'Корпоративный шаблон.pptx', type: PPTX_MIME, content: templateBytes }])
    const template = await unwrap(
      api.POST('/api/v1/projects/{project_id}/templates', {
        params: { path: { project_id: project.id } },
        body: { file: 'Корпоративный шаблон.pptx' },
        bodySerializer: () => templateForm.body,
        headers: { 'Content-Type': templateForm.contentType },
      }),
    )
    expect(template).toMatchObject({ project_id: project.id, filename: 'Корпоративный шаблон.pptx', package_type: 'pptx', size_bytes: templateBytes.byteLength })
    expect(template.sha256).toMatch(/^[0-9a-f]{64}$/)

    const notReady = await api.GET('/api/v1/templates/{template_id}/design-dna', { params: { path: { template_id: template.id } } })
    expect(notReady.response.status).toBe(404)
    expect((notReady.error as ErrorEnvelope).error).toMatchObject({ code: 'not_found', retryable: false, details: expect.any(Object) })

    const analysis = await unwrap(api.POST('/api/v1/templates/{template_id}/analyze', { params: { path: { template_id: template.id } }, body: {} }))
    const analysisJob = await unwrap(api.GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: analysis.job_id } } }))
    expect(analysisJob).toMatchObject({ kind: 'template_analysis', state: 'completed', progress: 1 })
    const dna = await unwrap(api.GET('/api/v1/templates/{template_id}/design-dna', { params: { path: { template_id: template.id } } }))
    expect(dna).toMatchObject({ template_id: template.id, analysis_id: analysis.analysis_id })

    const contentForm = multipart([
      { name: 'files', filename: 'brief.md', type: 'text/markdown', content: '# Платформа отчётности\n\nКлючевые факты.' },
      { name: 'brief', content: JSON.stringify({ purpose: 'product', audience: 'руководство' }) },
    ])
    const ingestion = await unwrap(
      api.POST('/api/v1/projects/{project_id}/content-packs', {
        params: { path: { project_id: project.id } },
        body: { files: ['brief.md'] },
        bodySerializer: () => contentForm.body,
        headers: { 'Content-Type': contentForm.contentType },
      }),
    )
    expect(ingestion.job).toMatchObject({ kind: 'content_ingestion', state: 'completed' })
    expect(ingestion.content_pack.title_hint).toBe('Платформа отчётности')
    const linked = await unwrap(api.GET('/api/v1/projects/{project_id}', { params: { path: { project_id: project.id } } }))
    expect(linked).toMatchObject({ template_id: template.id, content_pack_id: ingestion.content_pack.id })

    const brief = { purpose: 'product', audience: 'руководство', language: 'ru', target_slide_count: 12 }
    const generation = await unwrap(
      api.POST('/api/v1/projects/{project_id}/generations', {
        params: { path: { project_id: project.id } },
        body: {
          template_id: template.id,
          content_pack_id: ingestion.content_pack.id,
          brief,
          variants: [{ strategy: 'faithful' }, { strategy: 'balanced' }, { strategy: 'visual' }],
          use_llm: false,
        },
      }),
    )
    expect(generation.variant_ids).toHaveLength(3)
    expect(generation.variant_ids).not.toContain(variantFixtures.balanced.variant.id)

    const detail = await unwrap(api.GET('/api/v1/generations/{run_id}', { params: { path: { run_id: generation.generation_id } } }))
    expect(detail.state).toBe('completed')
    expect(detail.deck_plan?.brief).toMatchObject(brief)
    expect(detail.variants.every((variant) => variant.status === 'completed' && variant.audit_status === 'completed')).toBe(true)

    const variants = await unwrap(api.GET('/api/v1/generations/{run_id}/variants', { params: { path: { run_id: generation.generation_id } } }))
    expect(variants.items.map((variant) => variant.strategy)).toEqual(['faithful', 'balanced', 'visual'])
    const balanced = variants.items[1]
    if (!balanced) throw new Error('balanced variant is missing')

    const slides = await unwrap(api.GET('/api/v1/variants/{variant_id}/slides', { params: { path: { variant_id: balanced.id } } }))
    expect(slides.items).toHaveLength(variantFixtures.balanced.slides.length)
    expect(slides.items.every((slide) => slide.variant_id === balanced.id)).toBe(true)

    const accepted = await unwrap(api.POST('/api/v1/variants/{variant_id}/audits', { params: { path: { variant_id: balanced.id } }, body: {} }))
    const audit = await unwrap(api.GET('/api/v1/audits/{audit_id}', { params: { path: { audit_id: accepted.audit_id } } }))
    expect(audit).toMatchObject({ variant_id: balanced.id, deck_revision: 1, issue_count: variantFixtures.balanced.issues.length })

    const listIssues = (query: IssueQuery) =>
      unwrap(api.GET('/api/v1/audits/{audit_id}/issues', { params: { path: { audit_id: audit.id }, query } })).then((result) => result.items)

    const all = await listIssues({ limit: 500 })
    expect(all).toHaveLength(variantFixtures.balanced.issues.length)
    expect(all.every((issue) => issue.audit_run_id === audit.id && issue.id.startsWith(`${audit.id}:`))).toBe(true)
    expect(await listIssues({ limit: 500, severity: 'error' })).toHaveLength(audit.summary_by_severity.error)
    expect(await listIssues({ limit: 500, deterministic: false })).toHaveLength(0)
    expect(await listIssues({ limit: 3 })).toHaveLength(3)
    const firstSlide = slides.items[0]
    if (!firstSlide) throw new Error('slide is missing')
    const onFirstSlide = await listIssues({ limit: 500, slide_id: firstSlide.id })
    expect(onFirstSlide.length).toBeGreaterThan(0)
    expect(onFirstSlide.every((issue) => issue.slide_index === 0)).toBe(true)

    const overflow = await listIssues({ limit: 500, rule_code: 'text.overflow', repairable: true })
    const selected = overflow.slice(0, 2).map((issue) => issue.id)
    expect(selected).toHaveLength(2)

    const repair = await unwrap(
      api.POST('/api/v1/audits/{audit_id}/repairs', { params: { path: { audit_id: audit.id } }, body: { selected_issue_ids: selected, max_iterations: 2 } }),
    )
    expect(repair).toMatchObject({ audit_id: audit.id, deck_revision: 2 })
    const repairJob = await unwrap(api.GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: 'job_id' in repair ? repair.job_id : '' } } }))
    expect(repairJob).toMatchObject({ kind: 'repair', state: 'completed' })
    expect(repairJob.result_ids).toMatchObject({ applied: '2', unresolved: '0', deck_revision: '2' })

    const fixed = await listIssues({ limit: 500, status: 'fixed' })
    expect(fixed.map((issue) => issue.id).sort()).toEqual([...selected].sort())
    expect(fixed.every((issue) => issue.deck_revision === 2)).toBe(true)
    const repaired = await unwrap(api.GET('/api/v1/audits/{audit_id}', { params: { path: { audit_id: audit.id } } }))
    expect(repaired).toMatchObject({ deck_revision: 2, issue_count: audit.issue_count - 2 })
    const repairedVariant = await unwrap(api.GET('/api/v1/variants/{variant_id}', { params: { path: { variant_id: balanced.id } } }))
    expect(repairedVariant.deck_artifact_id).not.toBe(balanced.deck_artifact_id)
    expect(repairedVariant.metrics?.issues_total).toBe(audit.issue_count - 2)

    const fontScale = (await listIssues({ limit: 1, rule_code: 'template.font_scale' }))[0]
    if (!fontScale) throw new Error('font scale issue is missing')
    const partial = await unwrap(
      api.POST('/api/v1/audits/{audit_id}/repairs', { params: { path: { audit_id: audit.id } }, body: { selected_issue_ids: [fontScale.id, selected[0] ?? ''], max_iterations: 2 } }),
    )
    const partialJob = await unwrap(api.GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: 'job_id' in partial ? partial.job_id : '' } } }))
    expect(partialJob.result_ids).toMatchObject({ applied: '0', skipped: '1', unresolved: '1' })
    expect((await listIssues({ limit: 500, status: 'unresolved' })).map((issue) => issue.id)).toEqual([fontScale.id])

    const dismissTarget = overflow[2]
    if (!dismissTarget) throw new Error('dismiss target is missing')
    await expectApiError(unwrap(api.POST('/api/v1/issues/{issue_id}/dismiss', { params: { path: { issue_id: dismissTarget.id } }, body: { reason: '' } })), 422, 'validation_error')
    const dismissed = await unwrap(api.POST('/api/v1/issues/{issue_id}/dismiss', { params: { path: { issue_id: dismissTarget.id } }, body: { reason: 'Осознанное решение' } }))
    expect(dismissed.status).toBe('dismissed')
    expect((await listIssues({ status: 'dismissed' })).map((issue) => issue.id)).toEqual([dismissTarget.id])

    const pdfExport = await unwrap(api.POST('/api/v1/variants/{variant_id}/exports', { params: { path: { variant_id: balanced.id } }, body: { formats: ['pdf', 'pptx'] } }))
    const exportRecord = await unwrap(api.GET('/api/v1/exports/{export_id}', { params: { path: { export_id: pdfExport.export_id } } }))
    expect(exportRecord).toMatchObject({ variant_id: balanced.id, deck_revision: 3 })
    expect(exportRecord.artifacts.map((artifact) => artifact.format)).toEqual(['pdf', 'pptx'])

    const [pdfArtifact, pptxArtifact] = exportRecord.artifacts
    if (!pdfArtifact || !pptxArtifact) throw new Error('export artifacts are missing')
    const pdf = await fetch(new URL(pdfArtifact.download_url, window.location.origin))
    expect(pdf.headers.get('content-type')).toBe('application/pdf')
    expect(pdf.headers.get('content-disposition')).toMatch(/^attachment; filename="deckdna-balanced-r3\.pdf"/)
    expect(Array.from(new Uint8Array(await pdf.arrayBuffer()))).toEqual(Array.from(PDF_BYTES))

    const pptx = await fetch(new URL(pptxArtifact.download_url, window.location.origin))
    expect(pptx.headers.get('content-type')).toBe(PPTX_MIME)
    const pptxBytes = new Uint8Array(await pptx.arrayBuffer())
    expect(pptxBytes.byteLength).toBe(pptxArtifact.size_bytes)
    expect(Array.from(pptxBytes.slice(0, 2))).toEqual([0x50, 0x4b])

    const html = await api.POST('/api/v1/variants/{variant_id}/exports', { params: { path: { variant_id: balanced.id } }, body: { formats: ['html'] } })
    expect(html.response.status).toBe(501)
    expect((html.error as ErrorEnvelope).error).toEqual({
      code: 'not_implemented',
      message: expect.any(String),
      stage: 'export',
      retryable: false,
      request_id: expect.stringMatching(/^req_/),
      details: expect.objectContaining({ format: 'html' }),
    })

    await expectApiError(unwrap(api.POST('/api/v1/generations/{run_id}/cancel', { params: { path: { run_id: generation.generation_id } } })), 409, 'state_conflict')
  })

  it('creates provider sessions without echoing the token and probes declared capabilities', async () => {
    const { api, unwrap } = client
    const session = await unwrap(
      api.POST('/api/v1/provider-sessions', {
        body: {
          label: 'Локальный vLLM',
          base_url: 'https://inference.example.com/v1/',
          api_token: 'secret-token',
          models: { text: 'Qwen/Qwen3-32B', vision: 'Qwen/Qwen2.5-VL-32B-Instruct' },
          capabilities: { structured_output: true, tool_calls: true, image_input: true, embeddings: false },
          timeout_seconds: 90,
          max_concurrency: 4,
          ttl_seconds: 3600,
        },
      }),
    )
    expect(JSON.stringify(session)).not.toContain('secret-token')
    expect(session.base_url).toBe('https://inference.example.com/v1')

    const probe = await unwrap(api.POST('/api/v1/provider-sessions/{session_id}/test', { params: { path: { session_id: session.id } } }))
    expect(Object.fromEntries(probe.results.map((result) => [result.capability, result.status]))).toEqual({
      structured_output: 'ok',
      tool_calls: 'skip',
      image_input: 'ok',
      embeddings: 'skip',
    })

    const audit = await unwrap(
      api.POST('/api/v1/variants/{variant_id}/audits', {
        params: { path: { variant_id: variantFixtures.visual.variant.id } },
        body: { provider_session_id: session.id },
      }),
    )
    const contextual = await unwrap(api.GET('/api/v1/audits/{audit_id}/issues', { params: { path: { audit_id: audit.audit_id }, query: { deterministic: false } } }))
    expect(contextual.items.length).toBeGreaterThan(0)
    expect(contextual.items.every((issue) => !issue.deterministic && issue.provenance?.model_id === 'Qwen/Qwen2.5-VL-32B-Instruct')).toBe(true)
    const visual = await unwrap(api.GET('/api/v1/variants/{variant_id}', { params: { path: { variant_id: variantFixtures.visual.variant.id } } }))
    expect(visual.contextual_issues).toBe(contextual.items.length)

    const removed = await api.DELETE('/api/v1/provider-sessions/{session_id}', { params: { path: { session_id: session.id } } })
    expect(removed.response.status).toBe(204)
    await expectApiError(unwrap(api.DELETE('/api/v1/provider-sessions/{session_id}', { params: { path: { session_id: session.id } } })), 404, 'not_found')
  })

  it('validates requests with the backend error envelope', async () => {
    const { api, unwrap } = client
    await expectApiError(unwrap(api.POST('/api/v1/projects', { body: { name: ' ', default_language: 'ru', target_slide_count: 12 } })), 422, 'validation_error')
    await expectApiError(unwrap(api.POST('/api/v1/projects', { body: { name: 'Слишком много', default_language: 'ru', target_slide_count: 99 } })), 422, 'validation_error')
    await expectApiError(unwrap(api.GET('/api/v1/generations/{run_id}', { params: { path: { run_id: 'run_missing' } } })), 404, 'not_found')
    await expectApiError(
      unwrap(api.GET('/api/v1/audits/{audit_id}/issues', { params: { path: { audit_id: variantFixtures.faithful.audit.id }, query: { severity: 'fatal' } } })),
      422,
      'validation_error',
    )
    const unknown = await fetch(new URL('/api/v1/unknown-route', window.location.origin))
    expect(unknown.status).toBe(404)
    expect(await unknown.json()).toMatchObject({ error: { code: 'not_found', retryable: false } })
  })
})
