import { CATEGORY_LABEL, CATEGORY_ORDER, isAutoFixable, ruleMeta, type RuleCategory } from '../model/rules'
import { CRITICAL_SEVERITIES, SEVERITY } from '../model/severity'
import type { AuditIssue, CheckKind, Severity } from '../model/types'

export function checkKind(issue: AuditIssue): CheckKind {
  return issue.deterministic ? 'D' : 'N'
}

export function isOpenIssue(issue: AuditIssue): boolean {
  return issue.status === 'open' || issue.status === 'selected'
}

export function isCriticalIssue(issue: AuditIssue): boolean {
  return CRITICAL_SEVERITIES.includes(issue.severity)
}

export function canAutoFix(issue: AuditIssue): boolean {
  return issue.repairable && isAutoFixable(issue.rule_code)
}

export function issueSignature(issue: Pick<AuditIssue, 'rule_code' | 'slide_index' | 'shape_ids' | 'fingerprint'>): string {
  const fingerprint = issue.fingerprint?.trim()
  if (fingerprint) return fingerprint
  return [issue.rule_code, issue.slide_index ?? '-', [...(issue.shape_ids ?? [])].sort().join('+')].join('|')
}

export function slideNumber(issue: AuditIssue): number | null {
  return issue.slide_index === null || issue.slide_index === undefined ? null : issue.slide_index + 1
}

export interface IssueFilter {
  kind: 'all' | CheckKind
  severity: 'all' | Severity
  status: 'all' | 'open' | 'resolved'
  fixableOnly: boolean
}

export const DEFAULT_ISSUE_FILTER: IssueFilter = { kind: 'all', severity: 'all', status: 'open', fixableOnly: false }

export function filterIssues(issues: readonly AuditIssue[], filter: IssueFilter): AuditIssue[] {
  return issues.filter((issue) => {
    if (filter.kind !== 'all' && checkKind(issue) !== filter.kind) return false
    if (filter.severity !== 'all' && issue.severity !== filter.severity) return false
    if (filter.status === 'open' && !isOpenIssue(issue)) return false
    if (filter.status === 'resolved' && isOpenIssue(issue)) return false
    if (filter.fixableOnly && !canAutoFix(issue)) return false
    return true
  })
}

export function sortIssues(issues: readonly AuditIssue[]): AuditIssue[] {
  return [...issues].sort(
    (a, b) => SEVERITY[a.severity].order - SEVERITY[b.severity].order || (a.slide_index ?? 0) - (b.slide_index ?? 0),
  )
}

export interface IssueGroup {
  key: string
  label: string
  issues: AuditIssue[]
}

export function groupBySlide(issues: readonly AuditIssue[], titleOf: (slideNumber: number) => string | undefined): IssueGroup[] {
  const groups = new Map<number, AuditIssue[]>()
  for (const issue of sortIssues(issues)) {
    const number = slideNumber(issue) ?? 0
    groups.set(number, [...(groups.get(number) ?? []), issue])
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a - b)
    .map(([number, items]) => {
      const title = titleOf(number)
      const label = number === 0 ? 'Вся колода' : `Слайд ${String(number).padStart(2, '0')}${title ? ` · ${title}` : ''}`
      return { key: `slide-${number}`, label, issues: items }
    })
}

export function groupByCategory(issues: readonly AuditIssue[]): IssueGroup[] {
  const byCategory = new Map<RuleCategory, AuditIssue[]>()
  for (const issue of sortIssues(issues)) {
    const category = ruleMeta(issue.rule_code).category
    byCategory.set(category, [...(byCategory.get(category) ?? []), issue])
  }
  return CATEGORY_ORDER.filter((category) => byCategory.has(category)).map((category) => ({
    key: category,
    label: CATEGORY_LABEL[category],
    issues: byCategory.get(category) ?? [],
  }))
}

export function countBySeverity(issues: readonly AuditIssue[]): Record<Severity, number> {
  const counts: Record<Severity, number> = { blocker: 0, error: 0, warning: 0, info: 0 }
  for (const issue of issues) counts[issue.severity] += 1
  return counts
}

export function formatMeasurement(value: AuditIssue['measured_value']): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/\.?0+$/, '')
  return value
}
