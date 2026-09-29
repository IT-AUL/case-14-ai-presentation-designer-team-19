import { API_BASE } from '@/shared/config'
import { artifactUrl } from '@/shared/api'
import type { ExportArtifact, ExportRecord, SlideInfo, VariantSummary } from '../model/types'

export type DeckFileFormat = 'pptx' | 'pdf' | 'html' | 'quality_passport'

export interface DeckFile {
  format: DeckFileFormat
  artifactId: string
  exportId: string | null
  deckRevision: number | null
  sizeBytes: number | null
  sha256: string | null
  mimeType: string | null
}

export type DeckFiles = Record<DeckFileFormat, DeckFile | null>

interface Candidate {
  record: ExportRecord
  artifact: ExportArtifact
}

function newestFirst(a: ExportRecord, b: ExportRecord): number {
  return b.deck_revision - a.deck_revision || Date.parse(b.created_at) - Date.parse(a.created_at)
}

function toDeckFile(format: DeckFileFormat, { record, artifact }: Candidate): DeckFile {
  return {
    format,
    artifactId: artifact.artifact_id,
    exportId: record.id,
    deckRevision: record.deck_revision,
    sizeBytes: artifact.size_bytes,
    sha256: artifact.sha256,
    mimeType: artifact.mime_type,
  }
}

function pick(candidates: readonly Candidate[], format: DeckFileFormat): DeckFile | null {
  const candidate = candidates.find(({ artifact }) => artifact.format === format)
  return candidate ? toDeckFile(format, candidate) : null
}

function currentDeck(deckArtifactId: string | null | undefined, candidates: readonly Candidate[]): DeckFile | null {
  if (!deckArtifactId) return pick(candidates, 'pptx')
  const exported = candidates.find(({ artifact }) => artifact.artifact_id === deckArtifactId)
  if (exported) return toDeckFile('pptx', exported)
  return { format: 'pptx', artifactId: deckArtifactId, exportId: null, deckRevision: null, sizeBytes: null, sha256: null, mimeType: null }
}

export function resolveDeckFiles(variant: Pick<VariantSummary, 'id' | 'deck_artifact_id'>, exports: readonly ExportRecord[]): DeckFiles {
  const candidates = exports
    .filter((record) => record.variant_id === variant.id)
    .sort(newestFirst)
    .flatMap((record) => record.artifacts.map((artifact) => ({ record, artifact })))

  return {
    pptx: currentDeck(variant.deck_artifact_id, candidates),
    pdf: pick(candidates, 'pdf'),
    html: pick(candidates, 'html'),
    quality_passport: pick(candidates, 'quality_passport'),
  }
}

export function slidePreviewUrl(slide: Pick<SlideInfo, 'id' | 'revision' | 'preview_artifact_id'> | null | undefined, renderOnDemand = false): string | null {
  if (!slide) return null
  if (slide.preview_artifact_id) return artifactUrl(slide.preview_artifact_id)
  return renderOnDemand ? `${API_BASE}/slides/${encodeURIComponent(slide.id)}/preview?revision=${slide.revision}` : null
}
