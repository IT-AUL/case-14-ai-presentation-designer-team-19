import type { VariantSummary } from './types'

const SETTLED_STATUSES: readonly VariantSummary['status'][] = ['completed', 'failed', 'canceled']

export function isVariantSettled(variant: Pick<VariantSummary, 'status' | 'audit_status'>): boolean {
  return SETTLED_STATUSES.includes(variant.status) && variant.audit_status !== 'running'
}
