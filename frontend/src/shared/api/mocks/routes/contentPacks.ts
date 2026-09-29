import type { RequestHandler } from 'msw'
import type { MockContext } from '../context'
import { contentPackFixture } from '../fixtures'
import { json, MockApiError, validationError } from '../http'
import { randomHex, rebrand } from '../ids'
import type { ContentPack, EvidenceGraph, UploadedFile } from '../types'
import { formFiles, isRecord, readForm } from '../validate'
import type { JsonObject } from '../validate'

const SUPPORTED_EXTENSIONS = ['md', 'markdown', 'txt', 'json', 'docx', 'pdf', 'xlsx']
const TEXT_EXTENSIONS = new Set(['md', 'markdown', 'txt'])

function extensionOf(name: string): string {
  return name.toLowerCase().split('.').pop() ?? ''
}

function parseBrief(raw: FormDataEntryValue | null): JsonObject | null {
  if (raw === null || (typeof raw === 'string' && raw.trim() === '')) return null
  if (typeof raw !== 'string') throw validationError('brief', "Field 'brief' must be a JSON string")
  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    throw validationError('brief', "Field 'brief' is not valid JSON")
  }
  if (!isRecord(parsed)) throw validationError('brief', "Field 'brief' must be a JSON object")
  return parsed
}

function titleHint(file: UploadedFile, brief: JsonObject | null): string | null {
  for (const key of ['title', 'topic']) {
    const value = brief?.[key]
    if (typeof value === 'string' && value.trim() !== '') return value.trim()
  }
  if (!TEXT_EXTENSIONS.has(extensionOf(file.name))) return null
  const heading = new TextDecoder().decode(file.bytes).match(/^#\s+(.+)$/m)
  return heading?.[1]?.trim() ?? null
}

function buildPack(files: UploadedFile[], brief: JsonObject | null): ContentPack {
  const supported = files.filter((file) => SUPPORTED_EXTENSIONS.includes(extensionOf(file.name)))
  const [primary] = supported
  if (!primary) {
    throw new MockApiError(415, 'unsupported_media_type', `Supported content formats: ${SUPPORTED_EXTENSIONS.join(', ')}`, {
      stage: 'ingest',
      details: { filenames: files.map((file) => file.name) },
    })
  }
  const source = contentPackFixture.content_pack
  const id = `pack-${randomHex(8)}`
  const pack = rebrand(source, new Map([[source.id, id]]))
  const warnings = [...pack.warnings]
  for (const file of files) {
    if (file === primary) continue
    const skippedReason = supported.includes(file) ? 'only the first supported file is parsed' : 'format is not supported'
    warnings.push({ code: 'file_skipped', message: `${file.name}: ${skippedReason}`, source_ref: null })
  }
  return { ...pack, title_hint: titleHint(primary, brief) ?? pack.title_hint, warnings }
}

function evidenceGraph(pack: ContentPack): EvidenceGraph {
  const nodes: EvidenceGraph['nodes'] = []
  const edges: EvidenceGraph['edges'] = []
  for (const section of pack.sections) {
    nodes.push({ id: section.id, type: 'section', text: section.heading, value: null, source_ref: { artifact_id: pack.id }, confidence: 1 })
    section.blocks.forEach((block, blockIndex) => {
      const texts = block.items && block.items.length > 0 ? block.items : block.text ? [block.text] : []
      texts.forEach((text, itemIndex) => {
        const id = `${section.id}:b${blockIndex}:${itemIndex}`
        nodes.push({ id, type: 'claim', text, value: null, source_ref: { artifact_id: pack.id }, confidence: 1 })
        edges.push({ from: id, to: section.id, type: 'belongs_to', weight: 1 })
      })
    })
  }
  return { schema_version: '1.0', id: `eg-${pack.id}`, content_pack_id: pack.id, nodes, edges }
}

export function contentPackRoutes({ store, router }: MockContext): RequestHandler[] {
  const { route } = router
  return [
    route('post', '/projects/:projectId/content-packs', async ({ request, param }) => {
      const project = store.project(param('projectId'))
      const form = await readForm(request)
      const files = await formFiles(form, 'files')
      if (files.length === 0) throw validationError('files', "Field 'files' must contain at least one file")
      const pack = buildPack(files, parseBrief(form.get('brief')))
      store.contentPacks.set(pack.id, { projectId: project.id, pack })
      store.touchProject(project.id, { content_pack_id: pack.id })
      const job = store.createJob('content_ingestion', project.id, {
        resultIds: { content_pack_id: pack.id, evidence_graph_id: `eg-${pack.id}` },
      })
      return json({ content_pack: pack, job }, 202)
    }),

    route('get', '/content-packs/:packId', ({ param }) => json(store.contentPack(param('packId')).pack)),

    route('get', '/content-packs/:packId/evidence-graph', ({ param }) => json(evidenceGraph(store.contentPack(param('packId')).pack))),
  ]
}
