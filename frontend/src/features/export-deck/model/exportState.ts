import type { CreateExportResult, ExportFormat } from '@/entities/passport'
import type { DeckFile } from '@/entities/variant'

export type ExportStatus = 'idle' | 'creating' | 'ready' | 'unavailable' | 'error'

export interface ExportMutationSnapshot {
  status: 'idle' | 'pending' | 'success' | 'error'
  data?: CreateExportResult
  error?: unknown
}

export interface ExportStateInput {
  format: ExportFormat
  file: DeckFile | null
  currentRevision: number | null
  supported: boolean
  mutation: ExportMutationSnapshot
}

export interface ExportState {
  status: ExportStatus
  file: DeckFile | null
  stale: boolean
  message: string | null
}

export const FORMAT_LABEL: Record<ExportFormat, string> = {
  pptx: 'PPTX',
  pdf: 'PDF',
  html: 'HTML',
  quality_passport: 'JSON',
}

const FORMAT_NAME: Record<ExportFormat, string> = {
  pptx: 'PPTX',
  pdf: 'PDF',
  html: 'HTML',
  quality_passport: 'Паспорт',
}

export function fileFromResult(format: ExportFormat, result: CreateExportResult | undefined): DeckFile | null {
  if (result?.status !== 'created' || !result.record) return null
  const artifact = result.record.artifacts.find((item) => item.format === format)
  if (!artifact) return null
  return {
    format,
    artifactId: artifact.artifact_id,
    exportId: result.record.id,
    deckRevision: result.record.deck_revision,
    sizeBytes: artifact.size_bytes,
    sha256: artifact.sha256,
    mimeType: artifact.mime_type,
  }
}

export function isStaleFile(file: DeckFile | null, currentRevision: number | null): boolean {
  return Boolean(file && file.deckRevision !== null && currentRevision !== null && file.deckRevision < currentRevision)
}

export function unavailableMessage(format: ExportFormat, hasPreviousFile: boolean): string {
  if (format === 'html' && !hasPreviousFile) return 'Не реализовано сервисом'
  if (hasPreviousFile) return `${FORMAT_NAME[format]} относится к прошлой ревизии`
  return `Экспорт ${FORMAT_NAME[format]} недоступен`
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message ? error.message : 'Не удалось собрать файл'
}

export function deriveExportState({ format, file, currentRevision, supported, mutation }: ExportStateInput): ExportState {
  const created = fileFromResult(format, mutation.data)
  const current = created ?? file
  const stale = isStaleFile(current, currentRevision)

  if (mutation.status === 'pending') return { status: 'creating', file: current, stale, message: null }
  if (mutation.status === 'success' && mutation.data?.status === 'unavailable') {
    return { status: 'unavailable', file: current, stale, message: unavailableMessage(format, Boolean(current)) }
  }
  if (mutation.status === 'error') return { status: 'error', file: current, stale, message: errorMessage(mutation.error) }
  if (!supported) return { status: 'unavailable', file: current, stale, message: unavailableMessage(format, Boolean(current)) }
  if (current && !stale) return { status: 'ready', file: current, stale, message: null }
  if (mutation.status === 'success' && !current) return { status: 'creating', file: null, stale, message: null }
  return { status: 'idle', file: current, stale, message: null }
}
