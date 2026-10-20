import type { IntakeKind } from '@/entities/assistant'
import { pluralize } from '@/shared/lib/format'

export interface DatasetMetaInput {
  title?: string | null
  file_name?: string | null
  file?: string | null
  row_count?: number | null
  columns?: readonly unknown[] | null
  profile_summary?: string | null
}

export interface ExistingFile {
  name: string
  kind: IntakeKind
  meta?: string
}

export function formatDatasetDimensions(dataset: DatasetMetaInput | null | undefined): string | null {
  if (!dataset) return null
  const profileRows = dataset.profile_summary ? Number(/(\d+)\s*строк/i.exec(dataset.profile_summary)?.[1] ?? 0) : 0
  const profileCols = dataset.profile_summary ? Number(/(\d+)\s*колон/i.exec(dataset.profile_summary)?.[1] ?? 0) : 0
  const rows = typeof dataset.row_count === 'number' && dataset.row_count > 0 ? dataset.row_count : profileRows > 0 ? profileRows : null
  const cols = Array.isArray(dataset.columns) && dataset.columns.length > 0 ? dataset.columns.length : profileCols > 0 ? profileCols : null
  const parts: string[] = []
  if (rows !== null) parts.push(`${rows} ${pluralize(rows, ['строка', 'строки', 'строк'])}`)
  if (cols !== null) parts.push(`${cols} ${pluralize(cols, ['колонка', 'колонки', 'колонок'])}`)
  return parts.length > 0 ? parts.join(' · ') : null
}

function stripExtension(name: string): string {
  return name.trim().toLowerCase().replace(/\.[^.]+$/, '')
}

export function datasetDimensionsForFile(
  fileName: string,
  datasets: readonly DatasetMetaInput[],
  totalDataFiles = 1,
): string | null {
  if (datasets.length === 0) return null
  const clean = fileName.trim().toLowerCase()
  const stem = stripExtension(fileName)
  const matched = datasets.filter((dataset) => {
    const rawFile = dataset.file_name ?? dataset.file ?? ''
    const dsFile = rawFile.trim().toLowerCase()
    const dsTitle = dataset.title?.trim().toLowerCase() ?? ''
    return (
      (dsFile && (dsFile === clean || stripExtension(dsFile) === stem)) ||
      (dsTitle && (dsTitle === clean || dsTitle === stem))
    )
  })
  const target = matched[0] ?? (totalDataFiles <= 1 && datasets.length === 1 ? datasets[0] : null)
  if (!target) return null
  if (matched.length > 1) {
    const totalRows = matched.reduce((sum, item) => sum + (typeof item.row_count === 'number' ? item.row_count : 0), 0)
    const maxCols = Math.max(...matched.map((item) => (Array.isArray(item.columns) ? item.columns.length : 0)))
    return formatDatasetDimensions({ row_count: totalRows, columns: Array.from({ length: maxCols }) })
  }
  return formatDatasetDimensions(target)
}

export function topicFromBrief(brief: Record<string, unknown> | undefined): string {
  const purpose = typeof brief?.purpose === 'string' ? brief.purpose.trim() : ''
  return /\s/.test(purpose) ? purpose : ''
}

export function existingFiles(input: {
  template: string | null | undefined
  brandbook: string | null | undefined
  datasets: readonly DatasetMetaInput[]
  sections: number
  hasText?: boolean
}): ExistingFile[] {
  const rows: ExistingFile[] = []
  if (input.template) rows.push({ name: input.template, kind: 'template' })
  for (const dataset of input.datasets) {
    const name = dataset.title?.trim() || dataset.file_name?.trim()
    if (!name) continue
    const meta = formatDatasetDimensions(dataset)
    rows.push(meta ? { name, kind: 'data', meta } : { name, kind: 'data' })
  }
  if ((input.sections > 0 && input.datasets.length === 0) || input.hasText) rows.push({ name: 'Текст материалов', kind: 'content' })
  if (input.brandbook) rows.push({ name: input.brandbook, kind: 'brandbook' })
  return rows
}

export function withoutColorCodes(note: string): string {
  return note
    .replace(/,?\s*(?:главный|основной)\s+цвет\s+#[0-9a-f]{3,8}\b/gi, '')
    .replace(/\s*#[0-9a-f]{6}\b/gi, '')
    .replace(/\s+,/g, ',')
    .trim()
}
