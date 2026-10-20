import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ApiError, isUnsupportedEndpoint } from '@/shared/api'
import { API_BASE } from '@/shared/config'
import { createSseParser, isTerminalEvent } from '../lib/sse'
import type { JobEvent } from '../model/types'
import { assistantKeys } from './keys'

export interface SubscribeJobEventsOptions {
  after?: number
  onUnsupported?: () => void
  onEvent: (event: JobEvent) => void
  signal?: AbortSignal
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    signal.throwIfAborted()
    const abort = () => { clearTimeout(timer); reject(signal.reason) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, ms)
    signal.addEventListener('abort', abort, { once: true })
  })
}

export async function subscribeJobEvents(jobId: string, { after = 0, onEvent, onUnsupported, signal: externalSignal }: SubscribeJobEventsOptions): Promise<void> {
  let cursor = Number.isSafeInteger(after) && after >= 0 ? after : 0
  let terminal = false
  let failures = 0
  const controller = new AbortController()
  const abort = () => controller.abort(externalSignal?.reason)
  externalSignal?.addEventListener('abort', abort, { once: true })
  if (externalSignal?.aborted) abort()
  const { signal } = controller
  try {
    while (!terminal) {
      signal.throwIfAborted()
      let received = false
      let connectionError: unknown
      try {
        const response = await fetch(`${API_BASE}/jobs/${encodeURIComponent(jobId)}/events?after=${cursor}`, { headers: { accept: 'text/event-stream', 'Last-Event-ID': String(cursor) }, signal })
        if (!response.ok) {
          const body: unknown = await response.json().catch(() => undefined)
          throw ApiError.fromResponse(response.status, body)
        }
        if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) throw new ApiError({ code: 'invalid_stream', message: 'Не удалось прочитать ход сборки', status: 422 })
        const reader = response.body.getReader()
        const cancel = () => { void reader.cancel().catch(() => undefined) }
        signal.addEventListener('abort', cancel, { once: true })
        const decoder = new TextDecoder()
        const parse = createSseParser((event) => {
          if (event.sequence <= cursor || terminal) return
          cursor = event.sequence
          received = true
          failures = 0
          terminal = isTerminalEvent(event)
          onEvent(event)
        })
        try {
          while (!terminal) {
            const { value, done } = await reader.read()
            if (done) { parse(decoder.decode()); break }
            parse(decoder.decode(value, { stream: true }))
          }
        } finally {
          signal.removeEventListener('abort', cancel)
          await reader.cancel().catch(() => undefined)
          reader.releaseLock()
        }
      } catch (error) {
        if (signal.aborted) throw signal.reason
        if (isUnsupportedEndpoint(error)) { onUnsupported?.(); return }
        if (error instanceof ApiError && error.status >= 400 && error.status < 500) throw error
        connectionError = error
      }
      signal.throwIfAborted()
      if (!terminal) {
        if (!received && ++failures > 5) throw connectionError ?? new ApiError({ code: 'stream_disconnected', message: 'Связь со сборкой прервалась', status: 0, retryable: true })
        await pause(Math.min(250 * 2 ** Math.max(0, failures - 1), 8000), signal)
      }
    }
  } finally {
    externalSignal?.removeEventListener('abort', abort)
    controller.abort()
  }
}

interface EventsState {
  events: JobEvent[]
  lastEvent: JobEvent | null
  done: boolean
}
const empty: EventsState = { events: [], lastEvent: null, done: false }

export function useJobEvents(jobId: string | null | undefined) {
  const client = useQueryClient()
  const key = assistantKeys.events(jobId ?? '')
  const logKey = assistantKeys.eventLog(jobId ?? '')
  const query = useQuery<EventsState>({
    queryKey: key, enabled: Boolean(jobId), staleTime: Infinity, retry: false, refetchOnWindowFocus: false,
    refetchOnMount: (query) => query.state.data?.done ? false : 'always',
    refetchOnReconnect: (query) => query.state.data?.done ? false : 'always',
    queryFn: async ({ signal }): Promise<EventsState> => {
      let state = client.getQueryData<EventsState>(logKey) ?? client.getQueryData<EventsState>(key) ?? empty
      if (state.done) return state
      await subscribeJobEvents(jobId as string, { after: state.lastEvent?.sequence ?? 0, signal, onEvent: (event) => {
        state = { events: [...state.events, event], lastEvent: event, done: isTerminalEvent(event) }
        client.setQueryData(logKey, state)
        client.setQueryData(key, state)
        if (state.done) {
          for (const queryKey of [assistantKeys.sessions, ['assistant', 'style-preview'], ['assistant', 'plan'], ['plan'], ['generation'], ['variant'], ['audit'], ['export'], ['passport'], ['pdf'], ['assistant', 'slide'], ['job', jobId]]) void client.invalidateQueries({ queryKey })
        }
      } })
      return state
    },
  })
  return { ...(query.data ?? client.getQueryData<EventsState>(logKey) ?? empty), error: query.error }
}
