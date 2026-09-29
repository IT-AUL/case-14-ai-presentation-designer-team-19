import { ruleMeta, type RuleCategory } from '../model/rules'
import { SEVERITY } from '../model/severity'
import type { AuditIssue, Severity } from '../model/types'
import { canAutoFix, checkKind, formatMeasurement, groupByCategory, groupBySlide, isCriticalIssue, issueSignature, type IssueFilter } from './issues'
import type { JournalEntry } from './journal'

export type DisplayStatus = 'open' | 'selected' | 'fixed' | 'unresolved' | 'dismissed'

export interface IssueView {
  key: string
  issue: AuditIssue
  status: DisplayStatus
  note: JournalEntry | null
  selectable: boolean
}

export const PENDING_STATUSES: readonly DisplayStatus[] = ['open', 'selected', 'unresolved']

export function isPendingStatus(status: DisplayStatus): boolean {
  return PENDING_STATUSES.includes(status)
}

function latestBySignature(journal: readonly JournalEntry[]): Map<string, JournalEntry> {
  const latest = new Map<string, JournalEntry>()
  for (const entry of journal) {
    const previous = latest.get(entry.signature)
    if (!previous || previous.at < entry.at || (previous.at === entry.at && entry.outcome !== 'fixed')) latest.set(entry.signature, entry)
  }
  return latest
}

function currentView(issue: AuditIssue, latest: JournalEntry | undefined): IssueView {
  const base = { key: issue.id, issue, note: latest ?? null }
  if (issue.status === 'dismissed') return { ...base, status: 'dismissed', note: latest?.outcome === 'dismissed' ? latest : null, selectable: false }
  if (issue.status === 'fixed') return { ...base, status: 'fixed', selectable: false }
  if (issue.status === 'unresolved' || latest?.outcome === 'unresolved') return { ...base, status: 'unresolved', selectable: false }
  return { ...base, status: 'open', selectable: canAutoFix(issue) }
}

export function buildIssueViews(issues: readonly AuditIssue[], journal: readonly JournalEntry[]): IssueView[] {
  const latest = latestBySignature(journal)
  const current = issues.map((issue) => currentView(issue, latest.get(issueSignature(issue))))
  const reportedFixed = new Map<string, number>()
  for (const view of current) {
    if (view.status !== 'fixed') continue
    const signature = issueSignature(view.issue)
    reportedFixed.set(signature, (reportedFixed.get(signature) ?? 0) + 1)
  }
  const fixed: IssueView[] = []
  for (const entry of journal) {
    if (entry.outcome !== 'fixed') continue
    const reported = reportedFixed.get(entry.signature) ?? 0
    if (reported > 0) {
      reportedFixed.set(entry.signature, reported - 1)
      continue
    }
    fixed.push({ key: entry.id, issue: entry.issue, status: 'fixed', note: entry, selectable: false })
  }
  return [...current, ...fixed]
}

export function withSelection(views: readonly IssueView[], selectedIds: ReadonlySet<string>): IssueView[] {
  return views.map((view) => (view.selectable && selectedIds.has(view.issue.id) ? { ...view, status: 'selected' } : view))
}

export function selectableIds(views: readonly IssueView[]): string[] {
  return views.filter((view) => view.selectable).map((view) => view.issue.id)
}

export function pruneSelection(selected: ReadonlySet<string>, selectable: readonly string[]): Set<string> {
  const allowed = new Set(selectable)
  return new Set([...selected].filter((id) => allowed.has(id)))
}

export function toggleSelection(selected: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(selected)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}

export interface IssueViewFilter extends IssueFilter {
  category: 'all' | RuleCategory
}

export const DEFAULT_VIEW_FILTER: IssueViewFilter = { kind: 'all', severity: 'all', status: 'open', fixableOnly: false, category: 'all' }

export function filterIssueViews(views: readonly IssueView[], filter: IssueViewFilter): IssueView[] {
  return views.filter(({ issue, status, selectable }) => {
    if (filter.kind !== 'all' && checkKind(issue) !== filter.kind) return false
    if (filter.severity !== 'all' && issue.severity !== filter.severity) return false
    if (filter.category !== 'all' && ruleMeta(issue.rule_code).category !== filter.category) return false
    if (filter.status === 'open' && !isPendingStatus(status)) return false
    if (filter.status === 'resolved' && isPendingStatus(status)) return false
    if (filter.fixableOnly && !selectable) return false
    return true
  })
}

export interface IssueViewGroup {
  key: string
  label: string
  views: IssueView[]
  severity?: Severity
}

export type IssueGrouping = 'rule' | 'slide' | 'category'

function worstSeverity(views: readonly IssueView[]): Severity {
  return views.reduce<Severity>((worst, view) => (SEVERITY[view.issue.severity].order < SEVERITY[worst].order ? view.issue.severity : worst), 'info')
}

function groupByRule(views: readonly IssueView[]): IssueViewGroup[] {
  const byRule = new Map<string, IssueView[]>()
  const ordered = [...views].sort(
    (a, b) => SEVERITY[a.issue.severity].order - SEVERITY[b.issue.severity].order || (a.issue.slide_index ?? -1) - (b.issue.slide_index ?? -1),
  )
  for (const view of ordered) byRule.set(view.issue.rule_code, [...(byRule.get(view.issue.rule_code) ?? []), view])
  return [...byRule.entries()]
    .map(([ruleCode, items]) => ({ key: issueGroupKey(items[0]?.issue as AuditIssue, 'rule'), label: ruleMeta(ruleCode).name, views: items, severity: worstSeverity(items) }))
    .sort((a, b) => SEVERITY[a.severity].order - SEVERITY[b.severity].order || b.views.length - a.views.length || a.label.localeCompare(b.label, 'ru'))
}

export function issueGroupKey(issue: AuditIssue, grouping: IssueGrouping): string {
  if (grouping === 'rule') return `rule:${issue.rule_code}`
  if (grouping === 'slide') return `slide-${issue.slide_index === null || issue.slide_index === undefined ? 0 : issue.slide_index + 1}`
  return ruleMeta(issue.rule_code).category
}

export function groupIssueViews(
  views: readonly IssueView[],
  grouping: IssueGrouping,
  titleOf: (slideNumber: number) => string | undefined = () => undefined,
): IssueViewGroup[] {
  if (grouping === 'rule') return groupByRule(views)
  const byIssue = new Map(views.map((view) => [view.issue, view]))
  const issues = views.map((view) => view.issue)
  const groups = grouping === 'slide' ? groupBySlide(issues, titleOf) : groupByCategory(issues)
  return groups.map((group) => ({
    key: group.key,
    label: group.label,
    views: group.issues.map((issue) => byIssue.get(issue)).filter((view): view is IssueView => Boolean(view)),
  }))
}

export interface IssueCluster {
  key: string
  primary: IssueView
  views: IssueView[]
}

function clusterSignature(view: IssueView): string {
  const { issue } = view
  return [
    issue.rule_code,
    issue.slide_index ?? '-',
    formatMeasurement(issue.measured_value),
    formatMeasurement(issue.threshold),
    issue.severity,
    isPendingStatus(view.status) ? 'pending' : view.status,
    view.selectable ? 'auto' : 'manual',
  ].join('|')
}

export function clusterIssueViews(views: readonly IssueView[]): IssueCluster[] {
  const clusters = new Map<string, IssueView[]>()
  for (const view of views) {
    const signature = clusterSignature(view)
    const bucket = clusters.get(signature)
    if (bucket) bucket.push(view)
    else clusters.set(signature, [view])
  }
  return [...clusters.values()].map((items) => {
    const primary = items[0] as IssueView
    return { key: primary.key, primary, views: items }
  })
}

export type SelectionState = 'none' | 'some' | 'all'

export function selectionState(views: readonly IssueView[], selectedIds: ReadonlySet<string>): SelectionState {
  const selectable = views.filter((view) => view.selectable)
  const selected = selectable.filter((view) => selectedIds.has(view.issue.id)).length
  if (selected === 0) return 'none'
  return selected === selectable.length ? 'all' : 'some'
}

export function countOpenCritical(views: readonly IssueView[]): number {
  return views.filter((view) => isPendingStatus(view.status) && isCriticalIssue(view.issue)).length
}

export function countOpenBySeverity(views: readonly IssueView[]): Record<Severity, number> {
  const counts: Record<Severity, number> = { blocker: 0, error: 0, warning: 0, info: 0 }
  for (const view of views) if (isPendingStatus(view.status)) counts[view.issue.severity] += 1
  return counts
}

export function countByCategory(views: readonly IssueView[]): Record<RuleCategory, number> {
  const counts: Record<RuleCategory, number> = { brand: 0, layout: 0, text: 0, density: 0, integrity: 0, meaning: 0 }
  for (const view of views) {
    if (isPendingStatus(view.status)) counts[ruleMeta(view.issue.rule_code).category] += 1
  }
  return counts
}
