import type { VariantAuditStatus, VariantSummary } from '../model/types'

export const PEI_MAX = 5

export interface VariantMetricsView {
  validity: number | null
  opensCleanly: boolean | null
  editabilityLevel: number | null
  editabilityFraction: number | null
  issuesTotal: number | null
  contextualIssues: number | null
  styleFidelity: number | null
}

function finite(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function fraction(value: number | null | undefined): number | null {
  const number = finite(value)
  return number === null ? null : Math.min(1, Math.max(0, number))
}

export function variantMetrics(variant: Pick<VariantSummary, 'metrics' | 'contextual_issues'>): VariantMetricsView {
  const validity = finite(variant.metrics?.validity)
  const editabilityLevel = finite(variant.metrics?.editability_pei)
  return {
    validity,
    opensCleanly: validity === null ? null : validity >= 1,
    editabilityLevel,
    editabilityFraction: editabilityLevel === null ? null : Math.min(1, Math.max(0, editabilityLevel / PEI_MAX)),
    issuesTotal: finite(variant.metrics?.issues_total),
    contextualIssues: finite(variant.contextual_issues),
    styleFidelity: fraction(variant.metrics?.style_fidelity),
  }
}

export const AUDIT_STATUS_LABEL: Record<VariantAuditStatus, string> = {
  not_started: 'Аудит не запускался',
  running: 'Идёт аудит',
  completed: 'Аудит готов',
  failed: 'Аудит не удался',
}
