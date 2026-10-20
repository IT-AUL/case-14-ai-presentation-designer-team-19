import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { setupServer } from 'msw/node'
import { http, HttpResponse } from 'msw'
import type * as ChatModule from './chat'
let useAgentRun: typeof ChatModule.useAgentRun
import { assistantKeys } from './keys'

const server = setupServer()
beforeAll(async () => {
  server.listen({ onUnhandledRequest: 'error' })
  ;({ useAgentRun } = await import('./chat'))
})
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
function setup(unsupported = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  let controller: ReadableStreamDefaultController<Uint8Array> | undefined
  let sequence = 0
  server.use(
    http.get('*/api/v1/jobs/job/events', () =>
      unsupported
        ? new HttpResponse(null, { status: 404 })
        : new HttpResponse(
            new ReadableStream({
              start(c) {
                controller = c
              },
            }),
            { headers: { 'Content-Type': 'text/event-stream' } },
          ),
    ),
  )
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const hook = renderHook(() => useAgentRun('job', 'project'), { wrapper })
  const emit = async (type: string, data = {}) => {
    await waitFor(() => expect(controller).toBeDefined())
    await act(async () => {
      controller?.enqueue(
        new TextEncoder().encode(
          `id: ${++sequence}\nevent: ${type}\ndata: ${JSON.stringify({ sequence, type, data })}\n\n`,
        ),
      )
    })
  }
  return { ...hook, client, invalidate, emit }
}
it('refreshes slides, variant and generation immediately on structural revision before completion', async () => {
  const { emit, invalidate, result } = setup()
  await emit('deck.revision', { variant_id: 'v', revision: 2, slide_count: 4, structural: true })
  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['variant'] }))
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['generation'] })
  expect(result.current.done).toBe(false)
  await emit('job.completed')
  await waitFor(() => expect(result.current.done).toBe(true))
})
it('refreshes a changed slide preview and preserves the event cursor', async () => {
  const { emit, invalidate, client } = setup()
  await emit('slide.updated', { variant_id: 'v', slide_id: 's', index: 1, revision: 2, preview_artifact_id: 'new' })
  await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ['assistant', 'slide'] }))
  expect(client.getQueryData(assistantKeys.agentLog('job'))).toMatchObject({ lastSequence: 1 })
  await emit('job.completed')
})
it('quietly signals fallback when the stream endpoint is unavailable', async () => {
  const { result } = setup(true)
  await waitFor(() => expect(result.current.unsupported).toBe(true))
})
