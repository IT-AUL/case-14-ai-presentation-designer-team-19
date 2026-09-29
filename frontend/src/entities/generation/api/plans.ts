import { requestJson } from '@/shared/api'
import type { DeckPlan, PlanDraftResult, PlanProposal, PlanRequest } from '../model/types'

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isDeckPlan(value: unknown): value is DeckPlan {
  return isRecord(value) && Array.isArray(value.slides) && typeof value.id === 'string'
}

function toProposal(value: unknown): PlanProposal | null {
  if (isDeckPlan(value)) return { strategy: null, deckPlan: value }
  if (!isRecord(value)) return null
  const plan = value.deck_plan ?? value.plan
  if (!isDeckPlan(plan)) return null
  return { strategy: typeof value.strategy === 'string' ? value.strategy : null, deckPlan: plan }
}

export function parsePlanDraft(payload: unknown): PlanDraftResult {
  const planId = isRecord(payload) && typeof payload.plan_id === 'string' ? payload.plan_id : null
  const list = isRecord(payload) && Array.isArray(payload.plans) ? payload.plans : [payload]
  const proposals = list.map(toProposal).filter((proposal): proposal is PlanProposal => proposal !== null)
  return { planId, proposals }
}

export async function requestPlans(projectId: string, request: PlanRequest): Promise<PlanDraftResult> {
  const payload = await requestJson<unknown>(`/projects/${encodeURIComponent(projectId)}/plans`, { body: request })
  const result = parsePlanDraft(payload)
  if (result.proposals.length === 0) throw new Error('Сервис не вернул план колоды')
  return result
}
