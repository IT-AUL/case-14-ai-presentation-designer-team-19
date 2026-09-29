import type { Schemas } from '@/shared/api'

export type FixPreview = Schemas['FixPreview']

export type AuditIssue = Schemas['AuditIssueOut']
export type AuditRun = Schemas['AuditRun']
export type Severity = AuditIssue['severity']
export type IssueStatus = AuditIssue['status']
export type CheckKind = 'D' | 'N'

export type RepairIssueStatus = 'fixed' | 'failed' | 'skipped' | 'planned'

export interface RepairIssueOutcome {
  issueId: string
  status: RepairIssueStatus
  action: string | null
  summary: string | null
  reason: string | null
}
