import { useMutation } from '@tanstack/react-query'
import { previewRepair, type AuditIssue, type RepairIssueOutcome } from '@/entities/audit'
import { useActiveProviderSession } from '@/entities/provider-session'

export interface RepairPreviewItem {
  issue: AuditIssue
  outcome: RepairIssueOutcome | null
}

export interface RepairPreview {
  key: string
  items: RepairPreviewItem[]
}

export function selectionKey(issues: readonly AuditIssue[]): string {
  return issues
    .map((issue) => issue.id)
    .sort()
    .join('|')
}

export function useRepairPreview(auditId: string | undefined) {
  const session = useActiveProviderSession()
  return useMutation({
    mutationFn: async (selected: readonly AuditIssue[]): Promise<RepairPreview> => {
      if (!auditId) throw new Error('Аудит ещё не загружен')
      const outcomes = await previewRepair(
        auditId,
        selected.map((issue) => issue.id),
        session?.id,
      )
      const byIssue = new Map(outcomes.map((outcome) => [outcome.issueId, outcome]))
      return { key: selectionKey(selected), items: selected.map((issue) => ({ issue, outcome: byIssue.get(issue.id) ?? null })) }
    },
  })
}
