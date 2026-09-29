export { auditIssuesQuery, auditKeys, previewRepair, useAuditIssues, useContextualAudit, useDismissIssue, useRepairIssues, useVariantAudit } from './api/auditApi'
export type { RepairOutcome } from './api/auditApi'
export { loadRuleCatalog, resetRuleCatalog, useRuleCatalog } from './api/ruleCatalogApi'
export { parseRepairOutcomes } from './lib/outcomes'
export {
  canAutoFix,
  checkKind,
  countBySeverity,
  DEFAULT_ISSUE_FILTER,
  filterIssues,
  formatMeasurement,
  groupByCategory,
  groupBySlide,
  isCriticalIssue,
  isOpenIssue,
  issueSignature,
  slideNumber,
  sortIssues,
} from './lib/issues'
export type { IssueFilter, IssueGroup } from './lib/issues'
export { boxStyle, clampBox, MIN_BOX_SIZE } from './lib/bbox'
export type { OverlayBox } from './lib/bbox'
export { diffRepair, dismissalEntry, journalActionText, plannedFix, summarizeJournal } from './lib/journal'
export type { JournalEntry, JournalOutcome, RepairBatch } from './lib/journal'
export { actionLabel, describeMeasurement, measurementSentence } from './lib/measurement'
export type { MeasurementView } from './lib/measurement'
export { clearOpenCounts, readOpenCounts, reportOpenCounts, useOpenCounts } from './lib/openCounts'
export type { OpenCounts } from './lib/openCounts'
export {
  buildIssueViews,
  clusterIssueViews,
  countByCategory,
  countOpenBySeverity,
  countOpenCritical,
  DEFAULT_VIEW_FILTER,
  filterIssueViews,
  groupIssueViews,
  isPendingStatus,
  issueGroupKey,
  pruneSelection,
  selectableIds,
  selectionState,
  toggleSelection,
  withSelection,
} from './lib/views'
export type { DisplayStatus, IssueCluster, IssueGrouping, IssueView, IssueViewFilter, IssueViewGroup, SelectionState } from './lib/views'
export { applyRuleCatalog, CATEGORY_LABEL, CATEGORY_ORDER, hasRuleCatalog, isAutoFixable, listRules, parseRuleCatalog, ruleMeta, RULES } from './model/rules'
export type { RuleCatalogEntry, RuleCategory, RuleMeta } from './model/rules'
export { CRITICAL_SEVERITIES, SEVERITY, SEVERITY_ORDER } from './model/severity'
export type { AuditIssue, AuditRun, CheckKind, FixPreview, IssueStatus, RepairIssueOutcome, RepairIssueStatus, Severity } from './model/types'
export { CheckKindBadge } from './ui/CheckKindBadge'
export { SeverityBadge } from './ui/SeverityBadge'
