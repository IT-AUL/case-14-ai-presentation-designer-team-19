import { isCriticalIssue, isOpenIssue, type AuditIssue, type AuditRun } from '@/entities/audit'
import type { ExportFormat, QualityPassport } from '@/entities/passport'
import { isCapabilityAvailable, type Capabilities } from '@/entities/system'
import { DEFAULT_STRATEGY, orderVariants, type DeckFile, type VariantSummary } from '@/entities/variant'
import { formatNumber, pluralize } from '@/shared/lib/format'

const HTML_CAPABILITY_PATHS = ['features.html_export', 'exporters.html', 'exports.html', 'exports.formats.html', 'export_formats.html', 'formats.html', 'export.html', 'html_export'] as const

const EXTENSION: Record<ExportFormat, string> = {
  pptx: 'pptx',
  pdf: 'pdf',
  html: 'html',
  quality_passport: 'json',
}

export function pickVariantId(variants: readonly VariantSummary[], requested: string | undefined): string | null {
  if (requested && variants.some((variant) => variant.id === requested)) return requested
  const ordered = orderVariants(variants)
  return ordered.find((variant) => variant.strategy === DEFAULT_STRATEGY)?.id ?? ordered[0]?.id ?? requested ?? null
}

export function htmlExportSupported(capabilities: Capabilities | null | undefined): boolean {
  return HTML_CAPABILITY_PATHS.some((path) => isCapabilityAvailable(capabilities, path))
}

function readVersion(source: Record<string, unknown> | null | undefined, keys: readonly string[]): string | null {
  if (!source) return null
  for (const key of keys) {
    const value = source[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

export function serverSkillVersion(manifest: Record<string, unknown> | null | undefined, version: Record<string, unknown> | null | undefined): string | null {
  return readVersion(manifest, ['skill_version', 'version']) ?? readVersion(version, ['skill_version', 'skill'])
}

export function exportFileName(strategy: string, format: ExportFormat, revision: number | null, mimeType: string | null = null): string {
  const base = format === 'quality_passport' ? 'passport' : `deckdna_${strategy}`
  const suffix = revision === null ? '' : `_r${revision}`
  if (format === 'html' && !mimeType?.includes('text/html')) return `${base}${suffix}_html.zip`
  return `${base}${suffix}.${EXTENSION[format]}`
}

export function withPassportMeta(file: DeckFile | null, passport: QualityPassport | null, passportFresh: boolean): DeckFile | null {
  if (!file || !passport || !passportFresh || (file.sizeBytes !== null && file.sha256 !== null)) return file
  const recorded = passport.exports.find((item) => item.format === file.format)
  if (!recorded) return file
  return { ...file, sizeBytes: file.sizeBytes ?? recorded.sizeBytes, sha256: file.sha256 ?? recorded.sha256 }
}

export function currentRevisionOf(auditRevision: number | null | undefined, files: readonly (DeckFile | null)[]): number | null {
  const revisions = [auditRevision, ...files.map((file) => file?.deckRevision)].filter((value): value is number => typeof value === 'number')
  return revisions.length > 0 ? Math.max(...revisions) : null
}

export interface OpenCritical {
  blockers: number
  errors: number
}

export function openCriticalCounts(issues: readonly AuditIssue[] | undefined, audit: Pick<AuditRun, 'summary_by_severity'> | undefined): OpenCritical | null {
  if (issues) {
    const open = issues.filter((issue) => isOpenIssue(issue) && isCriticalIssue(issue))
    return {
      blockers: open.filter((issue) => issue.severity === 'blocker').length,
      errors: open.filter((issue) => issue.severity === 'error').length,
    }
  }
  if (!audit) return null
  return { blockers: audit.summary_by_severity.blocker, errors: audit.summary_by_severity.error }
}

function openVerb(count: number, feminine: boolean): string {
  if (count % 10 === 1 && count % 100 !== 11) return feminine ? 'Открыта' : 'Открыт'
  return 'Открыто'
}

export function openCriticalText({ blockers, errors }: OpenCritical): string | null {
  const parts = [
    blockers > 0 && `${formatNumber(blockers)} ${pluralize(blockers, ['блокер', 'блокера', 'блокеров'])}`,
    errors > 0 && `${formatNumber(errors)} ${pluralize(errors, ['ошибка', 'ошибки', 'ошибок'])}`,
  ].filter(Boolean)
  if (parts.length === 0) return null
  const verb = blockers > 0 ? openVerb(blockers, false) : openVerb(errors, true)
  return `${verb} ${parts.join(' и ')}`
}
