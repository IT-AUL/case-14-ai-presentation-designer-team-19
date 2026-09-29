import { useMutation } from '@tanstack/react-query'
import { requestPlans, type PlanDraftResult, type PlanRequest } from '@/entities/generation'
import { readJson, writeJson } from '@/shared/lib/storage'

const STORAGE_PREFIX = 'deckdna.plan-request.v1:'

export type StoredRequest = PlanRequest & { template_id: string }

export interface StoredPlanRequest {
  request: StoredRequest
  result: PlanDraftResult
  createdAt: string
}

export function readPlanRequest(projectId: string): StoredPlanRequest | null {
  const stored = readJson<StoredPlanRequest | null>(`${STORAGE_PREFIX}${projectId}`, null)
  return stored && Array.isArray(stored.result?.proposals) && stored.result.proposals.length > 0 ? stored : null
}

export async function requestAndStorePlan(projectId: string, request: StoredRequest): Promise<StoredPlanRequest> {
  const result = await requestPlans(projectId, request)
  const stored: StoredPlanRequest = { request, result, createdAt: new Date().toISOString() }
  writeJson(`${STORAGE_PREFIX}${projectId}`, stored)
  return stored
}

export function useRequestPlan(projectId: string) {
  return useMutation({ mutationFn: (request: StoredRequest) => requestAndStorePlan(projectId, request) })
}
