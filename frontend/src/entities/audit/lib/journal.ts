import { ruleMeta } from '../model/rules'
import type { AuditIssue, FixPreview, RepairIssueOutcome } from '../model/types'
import { isOpenIssue, issueSignature } from './issues'
import { actionLabel } from './measurement'

export type JournalOutcome = 'fixed' | 'unresolved' | 'dismissed'

export interface JournalEntry {
  id: string
  outcome: JournalOutcome
  signature: string
  revision: number
  issue: AuditIssue
  reason: string | null
  detail?: string | null
  at: string
}

export interface RepairBatch {
  id: string
  fromRevision: number
  revision: number
  requested: number
  fixed: number
  unresolved: number
  serviceCounts: Record<string, number> | null
  at: string
}

function isStillPresent(issue: AuditIssue): boolean {
  return isOpenIssue(issue) || issue.status === 'unresolved'
}

const CLAIMED_BUT_PRESENT = 'Сервис сообщил об исправлении, но проблема осталась в новой ревизии'
const SKIPPED_WITHOUT_REASON = 'Сервис пропустил эту проблему'
const FAILED_WITHOUT_REASON = 'Исправление не сработало'

function outcomeReason(outcome: RepairIssueOutcome): string {
  if (outcome.reason) return outcome.reason
  return outcome.status === 'skipped' ? SKIPPED_WITHOUT_REASON : FAILED_WITHOUT_REASON
}

export function diffRepair(
  snapshot: readonly AuditIssue[],
  reaudit: readonly AuditIssue[],
  revision: number,
  at: string = new Date().toISOString(),
  outcomes: readonly RepairIssueOutcome[] | null = null,
): JournalEntry[] {
  const remaining = new Map<string, number>()
  for (const issue of reaudit) {
    if (!isStillPresent(issue)) continue
    const signature = issueSignature(issue)
    remaining.set(signature, (remaining.get(signature) ?? 0) + 1)
  }
  const byIssue = new Map((outcomes ?? []).map((outcome) => [outcome.issueId, outcome]))
  return snapshot.map((issue, index) => {
    const signature = issueSignature(issue)
    const left = remaining.get(signature) ?? 0
    if (left > 0) remaining.set(signature, left - 1)
    const present = left > 0
    const found = byIssue.get(issue.id)
    const reported = found?.status === 'planned' ? undefined : found
    const base = { id: `r${revision}:${index}:${signature}`, signature, revision, issue, at }
    if (!reported) return { ...base, outcome: present ? 'unresolved' : 'fixed', reason: null, detail: null }
    if (reported.status !== 'fixed') return { ...base, outcome: 'unresolved', reason: outcomeReason(reported), detail: reported.action }
    if (present) return { ...base, outcome: 'unresolved', reason: CLAIMED_BUT_PRESENT, detail: reported.summary }
    return { ...base, outcome: 'fixed', reason: null, detail: reported.summary }
  })
}

export function dismissalEntry(issue: AuditIssue, reason: string, revision: number, at: string = new Date().toISOString()): JournalEntry {
  const signature = issueSignature(issue)
  return { id: `d${revision}:${issue.id}:${at}`, outcome: 'dismissed', signature, revision, issue, reason, at }
}

export function summarizeJournal(entries: readonly JournalEntry[]): Record<JournalOutcome, number> {
  const latest = new Map<string, JournalEntry>()
  for (const entry of entries) {
    const key = entry.outcome === 'fixed' ? entry.id : entry.signature
    latest.set(key, entry)
  }
  const counts: Record<JournalOutcome, number> = { fixed: 0, unresolved: 0, dismissed: 0 }
  for (const entry of latest.values()) counts[entry.outcome] += 1
  return counts
}

function previewText(preview: FixPreview | null | undefined): string | null {
  const value = preview?.description_ru?.trim()
  return value ? value : null
}

export function plannedFix(issue: Pick<AuditIssue, 'rule_code' | 'proposed_actions' | 'fix_preview'>): string | null {
  const preview = previewText(issue.fix_preview)
  if (preview) return preview
  const autoFix = ruleMeta(issue.rule_code).autoFix
  if (autoFix) return autoFix
  const actions = issue.proposed_actions ?? []
  if (actions.length === 0) return null
  const text = actions.map(actionLabel).join(', ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export function journalActionText(entry: Pick<JournalEntry, 'outcome' | 'issue' | 'reason' | 'detail'>): string {
  if (entry.outcome === 'dismissed') return `Отклонено: ${entry.reason ?? 'причина сохранена в сервисе'}`
  if (entry.outcome === 'unresolved') return `Не удалось исправить: ${entry.reason ?? 'проблема осталась в новой ревизии'}`
  return entry.detail ?? plannedFix(entry.issue) ?? 'Исправлено'
}
