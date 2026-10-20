import { useMutation, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { api, isUnsupportedEndpoint, unwrap, ApiError } from '@/shared/api'
import { emptyAgentRun, reduceAgentRun, type AgentMessageRequest, type AgentRun, type AgentThread } from '../model/chat'
import { assistantKeys } from './keys'
import { subscribeJobEvents } from './events'

async function optional<T>(request: Promise<T>): Promise<T | null> {
  try {
    return await request
  } catch (error) {
    if (isUnsupportedEndpoint(error)) return null
    throw error
  }
}

export function useAgentThread(projectId: string) {
  const client = useQueryClient()
  return useQuery({
    queryKey: assistantKeys.thread(projectId),
    enabled: Boolean(projectId),
    staleTime: 0,
    queryFn: async ({ signal }) => {
      const previous = client.getQueryData<AgentThread | null>(assistantKeys.thread(projectId))
      const thread = await optional(
        unwrap(
          api.GET('/api/v1/projects/{project_id}/agent/thread', {
            params: { path: { project_id: projectId } },
            signal,
          }),
        ),
      )
      const latest = thread?.items.findLast((item) => item.role === 'assistant')
      if (
        thread &&
        !thread.active_job_id &&
        latest &&
        latest.id !== previous?.items.findLast((item) => item.role === 'assistant')?.id
      )
        await invalidateAgentContext(client)
      return thread
    },
  })
}

export function useSendAgentMessage(projectId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: AgentMessageRequest) =>
      optional(
        unwrap(
          api.POST('/api/v1/projects/{project_id}/agent/messages', {
            params: { path: { project_id: projectId } },
            body,
          }),
        ),
      ),
    onSuccess: async (data) => {
      if (!data) return
      client.setQueryData<AgentThread | null>(assistantKeys.thread(projectId), (thread) => ({
        items: thread?.items ?? [],
        active_job_id: data.job_id,
      }))
      await client.invalidateQueries({ queryKey: assistantKeys.thread(projectId) })
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 409) {
        const jobId = error.details.job_id
        if (typeof jobId === 'string')
          client.setQueryData<AgentThread | null>(assistantKeys.thread(projectId), (thread) => ({
            items: thread?.items ?? [],
            active_job_id: jobId,
          }))
        void client.invalidateQueries({ queryKey: assistantKeys.thread(projectId) })
      }
    },
  })
}

export function useCancelAgentMessage(projectId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) =>
      optional(
        unwrap(
          api.POST('/api/v1/projects/{project_id}/agent/jobs/{job_id}/cancel', {
            params: { path: { project_id: projectId, job_id: jobId } },
          }),
        ).then(() => true),
      ),
    onSuccess: async () => {
      await Promise.all([
        invalidateAgentDeck(client),
        client.invalidateQueries({ queryKey: ['assistant', 'plan'] }),
        client.invalidateQueries({ queryKey: ['assistant', 'understanding'] }),
      ])
      await client.invalidateQueries({ queryKey: assistantKeys.thread(projectId) })
    },
  })
}

export function useClearAgentThread(projectId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () =>
      optional(
        unwrap(
          api.DELETE('/api/v1/projects/{project_id}/agent/thread', { params: { path: { project_id: projectId } } }),
        ).then(() => true),
      ),
    onSuccess: (supported) => {
      if (supported)
        client.setQueryData(assistantKeys.thread(projectId), { items: [], active_job_id: null } satisfies AgentThread)
    },
  })
}

export function invalidateAgentDeck(client: QueryClient) {
  return Promise.all(
    [['variant'], ['generation'], ['assistant', 'slide'], ['audit'], ['export'], ['passport'], ['pdf']].map(
      (queryKey) => client.invalidateQueries({ queryKey }),
    ),
  )
}

async function invalidateAgentContext(client: QueryClient) {
  await Promise.all([
    invalidateAgentDeck(client),
    ...[
      assistantKeys.sessions,
      ['assistant', 'plan'],
      ['plan'],
      ['assistant', 'understanding'],
      ['assistant', 'questions'],
      ['content-pack'],
      ['assistant', 'style-preview'],
    ].map((queryKey) => client.invalidateQueries({ queryKey })),
  ])
}

export function useAgentRun(jobId: string | null | undefined, projectId: string) {
  const client = useQueryClient()
  const key = assistantKeys.agentRun(jobId ?? '')
  const logKey = assistantKeys.agentLog(jobId ?? '')
  const query = useQuery<AgentRun>({
    queryKey: key,
    enabled: Boolean(jobId),
    staleTime: Infinity,
    retry: false,
    refetchOnMount: (query) => (query.state.data?.done ? false : 'always'),
    refetchOnReconnect: (query) => (query.state.data?.done ? false : 'always'),
    queryFn: async ({ signal }) => {
      let state = client.getQueryData<AgentRun>(logKey) ?? emptyAgentRun
      if (state.done) return state
      await subscribeJobEvents(jobId as string, {
        signal,
        after: state.lastSequence,
        onUnsupported: () => {
          state = { ...state, unsupported: true }
          client.setQueryData(logKey, state)
          client.setQueryData(key, state)
        },
        onEvent: (event) => {
          state = reduceAgentRun(state, event)
          client.setQueryData(logKey, state)
          client.setQueryData(key, state)
          if (event.type === 'slide.updated' || event.type === 'deck.revision') void invalidateAgentDeck(client)
        },
      })
      if (state.done) {
        await invalidateAgentContext(client)
        client.setQueryData<AgentThread | null>(assistantKeys.thread(projectId), (thread) => {
          if (!thread || thread.active_job_id !== jobId) return thread
          const items = thread.items.filter((item) => item.id !== state.message?.id)
          return { items: state.message ? [...items, state.message] : items, active_job_id: null }
        })
        void client.invalidateQueries({ queryKey: assistantKeys.thread(projectId) })
      }
      return state
    },
  })
  return {
    ...(query.data ?? client.getQueryData<AgentRun>(logKey) ?? emptyAgentRun),
    error: query.error,
    retry: query.refetch,
  }
}
