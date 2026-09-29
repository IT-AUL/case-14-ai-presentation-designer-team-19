import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/shared/api'
import { parseRepairOutcomes, readCount } from '../lib/outcomes'
import type { AuditIssue, AuditRun, RepairIssueOutcome } from '../model/types'
import { loadRuleCatalog } from './ruleCatalogApi'

const ISSUE_PAGE_LIMIT = 200

export const auditKeys = {
  all: ['audit'] as const,
  forVariant: (variantId: string) => ['audit', 'variant', variantId] as const,
  issues: (auditId: string) => ['audit', auditId, 'issues'] as const,
}

async function fetchVariantAudit(variantId: string, providerSessionId?: string): Promise<AuditRun> {
  const accepted = await unwrap(
    api.POST('/api/v1/variants/{variant_id}/audits', {
      params: { path: { variant_id: variantId } },
      body: providerSessionId ? { provider_session_id: providerSessionId } : {},
    }),
  )
  return fetchAudit(accepted.audit_id)
}

function fetchAudit(auditId: string): Promise<AuditRun> {
  return unwrap(api.GET('/api/v1/audits/{audit_id}', { params: { path: { audit_id: auditId } } }))
}

export function auditIssuesQuery(auditId: string) {
  return queryOptions({
    queryKey: auditKeys.issues(auditId),
    queryFn: async (): Promise<AuditIssue[]> => {
      const [page] = await Promise.all([
        unwrap(
          api.GET('/api/v1/audits/{audit_id}/issues', {
            params: { path: { audit_id: auditId }, query: { limit: ISSUE_PAGE_LIMIT } },
          }),
        ),
        loadRuleCatalog(),
      ])
      return page.items
    },
  })
}

export function useVariantAudit(variantId: string | undefined) {
  return useQuery({
    queryKey: auditKeys.forVariant(variantId ?? ''),
    queryFn: () => fetchVariantAudit(variantId as string),
    enabled: Boolean(variantId),
  })
}

export function useAuditIssues(auditId: string | undefined) {
  return useQuery({ ...auditIssuesQuery(auditId ?? ''), enabled: Boolean(auditId) })
}

export interface RepairOutcome {
  auditId: string
  deckRevision: number
  counts: Record<'applied' | 'skipped' | 'failed' | 'not_implemented' | 'unresolved', number>
  outcomes: RepairIssueOutcome[] | null
}

export function useRepairIssues(variantId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async ({ auditId, issueIds, providerSessionId }: { auditId: string; issueIds: string[]; providerSessionId?: string }): Promise<RepairOutcome> => {
      const accepted = await unwrap(
        api.POST('/api/v1/audits/{audit_id}/repairs', {
          params: { path: { audit_id: auditId } },
          body: { selected_issue_ids: issueIds, max_iterations: 2, provider_session_id: providerSessionId ?? null },
        }),
      )
      if (!('job_id' in accepted)) throw new Error('Сервис вернул предпросмотр вместо запуска исправлений')
      const job = await unwrap(api.GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: accepted.job_id } } }))
      const read = (key: string) => readCount(job, key)
      return {
        auditId: accepted.audit_id,
        deckRevision: accepted.deck_revision,
        counts: {
          applied: read('applied'),
          skipped: read('skipped'),
          failed: read('failed'),
          not_implemented: read('not_implemented'),
          unresolved: read('unresolved'),
        },
        outcomes: parseRepairOutcomes(job, accepted),
      }
    },
    onSuccess: async (outcome) => {
      const audit = await fetchAudit(outcome.auditId).catch(() => null)
      if (audit) queryClient.setQueryData(auditKeys.forVariant(variantId), audit)
      else await queryClient.invalidateQueries({ queryKey: auditKeys.forVariant(variantId) })
    },
  })
}

export function useDismissIssue(auditId: string | undefined) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ issueId, reason }: { issueId: string; reason: string }) =>
      unwrap(api.POST('/api/v1/issues/{issue_id}/dismiss', { params: { path: { issue_id: issueId } }, body: { reason } })),
    onSuccess: (updated) => {
      if (!auditId) return
      queryClient.setQueryData<AuditIssue[]>(auditKeys.issues(auditId), (current) =>
        current?.map((issue) => (issue.id === updated.id ? updated : issue)),
      )
    },
  })
}

export function useContextualAudit(variantId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (providerSessionId?: string) => fetchVariantAudit(variantId, providerSessionId),
    onSuccess: async (audit) => {
      queryClient.setQueryData(auditKeys.forVariant(variantId), audit)
      await queryClient.invalidateQueries({ queryKey: auditKeys.issues(audit.id) })
    },
  })
}

export async function previewRepair(auditId: string, issueIds: readonly string[], providerSessionId?: string): Promise<RepairIssueOutcome[]> {
  const preview = await unwrap(
    api.POST('/api/v1/audits/{audit_id}/repairs', {
      params: { path: { audit_id: auditId }, query: { dry_run: true } },
      body: { selected_issue_ids: [...issueIds], max_iterations: 2, provider_session_id: providerSessionId ?? null },
    }),
  )
  return parseRepairOutcomes(preview) ?? []
}
