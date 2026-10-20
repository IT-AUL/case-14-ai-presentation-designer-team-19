import type { components } from '@/shared/api'
import type { JobEvent } from './types'

type Schemas = components['schemas']
export type AgentMessage = Schemas['AgentMessage']
export type AgentAction = Schemas['AgentAction']
export type AgentSource = Schemas['AgentSource']
export type AgentThread = Schemas['AgentThread']
export type AgentMessageRequest = Schemas['AgentMessageRequest']
export type AgentMessageAccepted = Schemas['AgentMessageAccepted']
export interface AgentStep extends Omit<AgentAction, 'ok' | 'summary'> {
  phase: 'started' | 'finished'
  text: string
  ok?: boolean
  summary?: string
}
export interface AgentRun {
  status: string | null
  steps: AgentStep[]
  message: AgentMessage | null
  lastSequence: number
  done: boolean
  failed: boolean
  unsupported: boolean
}
export const emptyAgentRun: AgentRun = {
  status: null,
  steps: [],
  message: null,
  lastSequence: 0,
  done: false,
  failed: false,
  unsupported: false,
}

export function reduceAgentRun(state: AgentRun, event: JobEvent): AgentRun {
  const next = { ...state, lastSequence: event.sequence }
  const data = event.data
  if (event.type === 'agent.status' && typeof data.text === 'string') return { ...next, status: data.text }
  if (
    event.type === 'agent.action' &&
    typeof data.tool === 'string' &&
    (data.phase === 'started' || data.phase === 'finished')
  ) {
    const step: AgentStep = {
      tool: data.tool,
      slide: typeof data.slide === 'number' ? data.slide : null,
      phase: data.phase,
      text: typeof data.text === 'string' ? data.text : '',
      ...(typeof data.ok === 'boolean' ? { ok: data.ok } : {}),
      ...(typeof data.summary === 'string' ? { summary: data.summary } : {}),
      ...(typeof data.revision === 'number' ? { revision: data.revision } : {}),
      ...(Array.isArray(data.sources) ? { sources: data.sources as AgentSource[] } : {}),
    }
    const index = state.steps.findIndex(
      (item) => item.phase === 'started' && item.tool === step.tool && item.slide === step.slide,
    )
    const steps = [...state.steps]
    if (step.phase === 'finished' && index >= 0)
      steps[index] = { ...steps[index], ...step, text: step.text || steps[index]?.text || '' }
    else steps.push(step)
    return { ...next, steps }
  }
  if (event.type === 'agent.message' && typeof data.message === 'object' && data.message !== null)
    return { ...next, message: data.message as AgentMessage }
  if (['job.completed', 'job.failed', 'job.canceled', 'job.cancelled'].includes(event.type))
    return { ...next, done: true, failed: event.type === 'job.failed', status: null }
  if (
    event.type === 'job.state_changed' &&
    ['completed', 'failed', 'canceled', 'cancelled'].includes(String(event.state ?? data.state))
  )
    return { ...next, done: true, failed: data.state === 'failed', status: null }
  return next
}

export type AgentEvent =
  | { type: 'agent.status'; data: { text: string; step: number } }
  | {
      type: 'agent.action'
      data:
        | { phase: 'started'; tool: string; slide: number | null; text: string }
        | (AgentAction & { phase: 'finished'; text: string })
    }
  | {
      type: 'slide.updated'
      data: {
        variant_id: string
        slide_id: string
        slide_key: string
        index: number
        revision: number
        preview_revision: number
        preview_artifact_id: string | null
        status: string
      }
    }
  | {
      type: 'deck.revision'
      data: {
        variant_id: string
        revision: number
        deck_artifact_id: string | null
        slide_count: number
        structural: boolean
      }
    }
  | { type: 'agent.message'; data: { message: AgentMessage } }
  | { type: 'job.completed'; data: { state: string; message_id: string; revision?: number | null } }
  | { type: 'job.failed'; data: { state: string; error: unknown; message_id: string } }
