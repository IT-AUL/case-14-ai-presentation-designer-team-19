import { variantFixtures } from './fixtures'
import { MockApiError, validationError } from './http'
import { nowIso, prefixedId } from './ids'
import { buildPassport } from './passport'
import { createPlaceholderPptx, PPTX_MIME } from './pptx'
import { exportArtifact } from './store'
import type { ArtifactRecord, MockStore, VariantRecord } from './store'
import type { ExportArtifact, ExportFormat, ExportRecord } from './types'

export const EXPORT_FORMATS: readonly ExportFormat[] = ['pptx', 'pdf', 'html', 'quality_passport']
export const AVAILABLE_EXPORT_FORMATS: readonly ExportFormat[] = ['pptx', 'pdf', 'quality_passport']

function baseName(variant: VariantRecord): string {
  return `deckdna-${variant.summary.strategy}-r${variant.revision}`
}

export async function createDeckArtifact(store: MockStore, variant: VariantRecord, id?: string): Promise<ArtifactRecord> {
  const slides = [
    {
      title: 'DeckDNA · демонстрационный режим',
      lines: [
        `Вариант «${variant.summary.strategy}», ревизия колоды ${variant.revision}.`,
        'Файл собран фронтендом без бэкенда: это структура колоды, а не вёрстка по шаблону.',
        'Подключите API DeckDNA, чтобы получить редактируемую колоду в стиле шаблона.',
      ],
    },
    ...variant.slides.map((slide) => ({
      title: slide.title ?? `Слайд ${slide.index + 1}`,
      lines: [`Слайд ${slide.index + 1} · ${slide.purpose ?? 'custom'}`],
    })),
  ]
  return store.storeBytes({ id, filename: `${baseName(variant)}.pptx`, mimeType: PPTX_MIME, bytes: createPlaceholderPptx(slides) })
}

function pdfArtifact(store: MockStore, variant: VariantRecord, id?: string): ArtifactRecord {
  const recorded = variantFixtures[variant.fixture].export.artifacts.find((artifact) => artifact.format === 'pdf')
  return store.storeArtifact({
    id: id ?? prefixedId('art'),
    filename: `${baseName(variant)}.pdf`,
    mimeType: 'application/pdf',
    sha256: recorded?.sha256 ?? '',
    sizeBytes: recorded?.size_bytes ?? 0,
    body: { kind: 'fixture-pdf', strategy: variant.fixture },
  })
}

async function passportArtifact(store: MockStore, variant: VariantRecord, id?: string): Promise<ArtifactRecord> {
  const deck = store.artifact(variant.summary.deck_artifact_id ?? '')
  const passport = await buildPassport(store, variant, deck)
  const bytes = new TextEncoder().encode(JSON.stringify(passport, null, 2))
  return store.storeBytes({ id, filename: `quality-passport-${variant.summary.strategy}-r${variant.revision}.json`, mimeType: 'application/json', bytes })
}

export function parseFormats(value: unknown): ExportFormat[] {
  if (!Array.isArray(value) || value.length === 0) throw validationError('formats', "Field 'formats' must be a non-empty array")
  const formats: ExportFormat[] = []
  for (const item of value) {
    const format = EXPORT_FORMATS.find((candidate) => candidate === item)
    if (!format) throw validationError('formats', `Unsupported export format '${String(item)}'`)
    if (!formats.includes(format)) formats.push(format)
  }
  if (formats.includes('html')) {
    throw new MockApiError(501, 'not_implemented', 'HTML export is not available through the API yet', {
      stage: 'export',
      details: { format: 'html', available_formats: AVAILABLE_EXPORT_FORMATS },
    })
  }
  return formats
}

interface ExportOptions {
  exportId?: string
  jobId: string
  artifactIds?: Partial<Record<ExportFormat, string>>
  createdAt?: string
}

export async function createExport(store: MockStore, variant: VariantRecord, formats: readonly ExportFormat[], options: ExportOptions): Promise<ExportRecord> {
  const artifacts: ExportArtifact[] = []
  for (const format of formats) {
    const id = options.artifactIds?.[format]
    if (format === 'pptx') {
      const deck = store.artifact(variant.summary.deck_artifact_id ?? '')
      artifacts.push(exportArtifact(format, deck))
    } else if (format === 'pdf') {
      artifacts.push(exportArtifact(format, pdfArtifact(store, variant, id)))
    } else if (format === 'quality_passport') {
      artifacts.push(exportArtifact(format, await passportArtifact(store, variant, id)))
    }
  }
  const record: ExportRecord = {
    id: options.exportId ?? prefixedId('exp'),
    variant_id: variant.summary.id,
    deck_revision: variant.revision,
    job_id: options.jobId,
    artifacts,
    created_at: options.createdAt ?? nowIso(),
  }
  store.exports.set(record.id, record)
  variant.summary = { ...variant.summary, export_ids: [...variant.summary.export_ids, record.id] }
  return record
}
