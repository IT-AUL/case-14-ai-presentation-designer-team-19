import type { RepairIssueOutcome, RepairIssueStatus } from '../model/types'

const STATUS_ALIASES: Record<string, RepairIssueStatus> = {
  fixed: 'fixed',
  applied: 'fixed',
  resolved: 'fixed',
  failed: 'failed',
  unresolved: 'failed',
  not_implemented: 'skipped',
  skipped: 'skipped',
  planned: 'planned',
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : null
}

function decode(value: unknown): unknown {
  if (typeof value !== 'string') return value
  try {
    return JSON.parse(value)
  } catch {
    return undefined
  }
}

function outcomeList(source: unknown): unknown[] | null {
  if (!isRecord(source)) return null
  const direct = decode(source.outcomes)
  if (Array.isArray(direct)) return direct
  for (const key of ['result', 'result_ids']) {
    const nested = outcomeList(source[key])
    if (nested) return nested
  }
  return null
}

function toOutcome(item: unknown): RepairIssueOutcome | null {
  if (!isRecord(item)) return null
  const issueId = text(item.issue_id)
  const status = STATUS_ALIASES[text(item.status)?.toLowerCase() ?? '']
  if (!issueId || !status) return null
  return { issueId, status, action: text(item.action), summary: text(item.summary), reason: text(item.reason) }
}

export function parseRepairOutcomes(...sources: unknown[]): RepairIssueOutcome[] | null {
  for (const source of sources) {
    const list = outcomeList(source)
    if (list) return list.map(toOutcome).filter((outcome): outcome is RepairIssueOutcome => outcome !== null)
  }
  return null
}

export function readCount(source: unknown, key: string): number {
  if (!isRecord(source)) return 0
  for (const container of [source.result, source.result_ids, source]) {
    if (!isRecord(container) || !(key in container)) continue
    const value = Number(container[key])
    if (Number.isFinite(value)) return value
  }
  return 0
}
