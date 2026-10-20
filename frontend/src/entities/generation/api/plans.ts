import { ApiError, requestJson } from '@/shared/api'
import { API_BASE } from '@/shared/config'
import { applyPlanEvent, EMPTY_PROGRESS, parseContentFill, parseResearch, parseSse, parseSufficiency, type PlanProgress } from '../lib/planExtras'
import { isTerminalState } from '../model/state'
import type { DeckPlan, Job, PlanDraftResult, PlanProposal, PlanRequest } from '../model/types'

export const PLAN_POLL_INTERVAL_MS = 1200

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
  const record = isRecord(payload) ? payload : {}
  const planId = typeof record.plan_id === 'string' ? record.plan_id : null
  const list = Array.isArray(record.plans) ? record.plans : [payload]
  const proposals = list.map(toProposal).filter((proposal): proposal is PlanProposal => proposal !== null)
  const contentFill = parseContentFill(record.content_fill, record)
  return {
    planId,
    proposals,
    sufficiency: parseSufficiency(record.sufficiency, record.content_fill),
    research: parseResearch(record.research, record.content_fill),
    ...(contentFill ? { contentFill } : {}),
  }
}

interface PlanAccepted {
  planId: string
  jobId: string
}

function acceptedPlan(payload: unknown): PlanAccepted | null {
  if (!isRecord(payload) || Array.isArray(payload.plans)) return null
  return typeof payload.plan_id === 'string' && typeof payload.job_id === 'string' ? { planId: payload.plan_id, jobId: payload.job_id } : null
}

export interface PlanRequestOptions {
  signal?: AbortSignal
  onProgress?: (progress: PlanProgress) => void
  pollIntervalMs?: number
}

function delay(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(signal.reason)
      return
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    const onAbort = () => {
      clearTimeout(timer)
      reject(signal?.reason)
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

async function readEvents(jobId: string, after: number, signal?: AbortSignal) {
  const response = await fetch(`${API_BASE}/jobs/${encodeURIComponent(jobId)}/events?after=${after}`, { headers: { accept: 'text/event-stream' }, signal })
  if (!response.ok) return []
  return parseSse(await response.text())
}

async function cancelPlan(projectId: string, planId: string): Promise<void> {
  try {
    await fetch(`${API_BASE}/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(planId)}/cancel`, { method: 'POST' })
  } catch {
    return
  }
}

async function followPlanJob(projectId: string, accepted: PlanAccepted, options: PlanRequestOptions): Promise<unknown> {
  const { signal, onProgress, pollIntervalMs = PLAN_POLL_INTERVAL_MS } = options
  const onAbort = () => void cancelPlan(projectId, accepted.planId)
  signal?.addEventListener('abort', onAbort, { once: true })
  let progress = EMPTY_PROGRESS
  let cursor = 0
  try {
    for (;;) {
      const events = await readEvents(accepted.jobId, cursor, signal)
      for (const event of events) {
        if (event.id !== null) cursor = Math.max(cursor, event.id)
        progress = applyPlanEvent(progress, event.type, event.data)
      }
      if (events.length > 0) onProgress?.(progress)
      const job = await requestJson<Job>(`/jobs/${encodeURIComponent(accepted.jobId)}`, { signal })
      if (job.stage && job.stage !== progress.stage) {
        progress = { ...progress, stage: job.stage }
        onProgress?.(progress)
      }
      if (isTerminalState(job.state)) {
        if (job.state !== 'completed') {
          throw new ApiError({ code: job.error?.code ?? job.state, message: job.error?.message ?? 'План не построен', status: 500, stage: job.error?.stage ?? 'plan' })
        }
        return requestJson<unknown>(`/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(accepted.planId)}`, { signal })
      }
      await delay(pollIntervalMs, signal)
    }
  } finally {
    signal?.removeEventListener('abort', onAbort)
  }
}

export async function requestPlans(projectId: string, request: PlanRequest, options: PlanRequestOptions = {}): Promise<PlanDraftResult> {
  const payload = await requestJson<unknown>(`/projects/${encodeURIComponent(projectId)}/plans`, {
    body: { ...request, respond_async: true },
    signal: options.signal,
  })
  const accepted = acceptedPlan(payload)
  const result = parsePlanDraft(accepted ? await followPlanJob(projectId, accepted, options) : payload)
  if (result.proposals.length === 0) throw new Error('Сервис не вернул план колоды')
  return result
}
