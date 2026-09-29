import { useQuery } from '@tanstack/react-query'
import { FEATURE_PATHS, supportsFeature } from '@/entities/system/@x/audit'
import { api, isUnsupportedEndpoint, unwrap } from '@/shared/api'
import { applyRuleCatalog, parseRuleCatalog, type RuleCatalogEntry } from '../model/rules'

let pending: Promise<RuleCatalogEntry[] | null> | null = null

async function fetchRuleCatalog(): Promise<unknown> {
  if (!(await supportsFeature(FEATURE_PATHS.ruleCatalog))) return null
  try {
    return await unwrap(api.GET('/api/v1/audit/rules'))
  } catch (error) {
    if (isUnsupportedEndpoint(error)) return null
    throw error
  }
}

export function loadRuleCatalog(): Promise<RuleCatalogEntry[] | null> {
  pending ??= fetchRuleCatalog()
    .then((payload) => {
      const entries = payload === null ? null : parseRuleCatalog(payload)
      applyRuleCatalog(entries)
      return entries
    })
    .catch(() => {
      pending = null
      return null
    })
  return pending
}

export function resetRuleCatalog(): void {
  pending = null
  applyRuleCatalog(null)
}

export function useRuleCatalog() {
  return useQuery({
    queryKey: ['audit', 'rules'] as const,
    queryFn: loadRuleCatalog,
    staleTime: Infinity,
  })
}
