import { CRITICAL_SEVERITIES, SEVERITY_ORDER, type Severity } from '@/entities/audit'
import type { PassportIssues, PassportScore } from '@/entities/passport'
import { PEI_MAX } from '@/entities/variant'
import { formatNumber, pluralize } from '@/shared/lib/format'

export type ValidityTone = 'ok' | 'error' | 'muted'

export interface ValidityView {
  label: string
  tone: ValidityTone
}

export interface SeverityCount {
  severity: Severity
  count: number
}

export function validityView(opensCleanly: boolean | null): ValidityView {
  if (opensCleanly === null) return { label: 'нет данных', tone: 'muted' }
  return opensCleanly ? { label: 'без ошибок', tone: 'ok' } : { label: 'с ошибками', tone: 'error' }
}

export function formatPei(level: number | null): string {
  return level === null ? '—' : `${level}/${PEI_MAX}`
}

export function peiNote(level: number | null, rasterOnlySlides: number | null): string | null {
  if (level === null) return null
  if (rasterOnlySlides !== null && rasterOnlySlides > 0) {
    return `${rasterOnlySlides} ${pluralize(rasterOnlySlides, ['слайд', 'слайда', 'слайдов'])} только растром`
  }
  if (level >= PEI_MAX) return 'всё нативное'
  if (rasterOnlySlides === 0) return 'без растровых слайдов'
  return null
}

export function issuesHeadline(total: number | null): string {
  if (total === null) return 'Проблемы не посчитаны'
  if (total === 0) return 'Проблем нет'
  return `${formatNumber(total)} ${pluralize(total, ['проблема', 'проблемы', 'проблем'])}`
}

export function severityBreakdown(total: number | null, issues: PassportIssues | null | undefined): SeverityCount[] | null {
  if (!issues) return null
  const counts = SEVERITY_ORDER.map((severity) => ({ severity, count: issues[severity] }))
  const sum = counts.reduce((acc, { count }) => acc + count, 0)
  if (total !== null && sum !== total) return null
  return counts
}

export function styleFidelityScore(direct: number | null, passportScores: readonly PassportScore[] | null | undefined): number | null {
  if (direct !== null) return direct
  if (!passportScores || passportScores.length === 0) return null
  const mean = passportScores.reduce((sum, { value }) => sum + value, 0) / passportScores.length
  return Math.min(1, Math.max(0, mean))
}

export function criticalCount(breakdown: readonly SeverityCount[] | null): number | null {
  if (!breakdown) return null
  return breakdown.filter(({ severity }) => CRITICAL_SEVERITIES.includes(severity)).reduce((sum, { count }) => sum + count, 0)
}

const SEVERITY_FORMS: Record<Severity, readonly [string, string, string]> = {
  blocker: ['блокер', 'блокера', 'блокеров'],
  error: ['ошибка', 'ошибки', 'ошибок'],
  warning: ['предупреждение', 'предупреждения', 'предупреждений'],
  info: ['замечание', 'замечания', 'замечаний'],
}

export function breakdownText(breakdown: readonly SeverityCount[] | null): string | null {
  if (!breakdown) return null
  const parts = breakdown.filter(({ count }) => count > 0).map(({ severity, count }) => `${formatNumber(count)} ${pluralize(count, SEVERITY_FORMS[severity])}`)
  return parts.length > 0 ? parts.join(', ') : null
}
