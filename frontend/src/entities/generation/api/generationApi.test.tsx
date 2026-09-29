import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { ApiError } from '@/shared/api'
import { RECONNECT_DELAYS_MS } from '@/shared/lib/polling'
import { POLL_INTERVAL_MS } from '../model/state'
import { generationKeys, useCancelGeneration, useGeneration } from './generationApi'
import * as requests from './requests'

vi.mock('./requests')

const running = { id: 'run_1', state: 'running', job_id: 'job_1', variants: [] } as never

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return { client, ...renderHook(() => useCancelGeneration(), { wrapper }) }
}

describe('useCancelGeneration', () => {
  it('reports a Russian message and refetches when the backend answers 409 after the check', async () => {
    vi.mocked(requests.fetchGeneration).mockResolvedValue(running)
    vi.mocked(requests.requestCancel).mockRejectedValue(
      new ApiError({ code: 'state_conflict', message: 'generation job is already completed; cannot cancel', status: 409 }),
    )
    const { client, result } = setup()
    const invalidate = vi.spyOn(client, 'invalidateQueries')

    result.current.mutate('run_1')

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error?.message).toBe('Генерация уже завершена, отменять нечего')
    expect(invalidate).toHaveBeenCalledWith({ queryKey: generationKeys.detail('run_1') })
  })

  it('passes other errors through unchanged', async () => {
    vi.mocked(requests.fetchGeneration).mockResolvedValue(running)
    vi.mocked(requests.requestCancel).mockRejectedValue(new ApiError({ code: 'internal_error', message: 'boom', status: 500 }))
    const { result } = setup()

    result.current.mutate('run_1')

    await waitFor(() => expect(result.current.isError).toBe(true))
    expect(result.current.error?.message).toBe('boom')
  })
})

describe('useGeneration polling', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('resumes polling after a 502 instead of stopping for good', async () => {
    vi.useFakeTimers()
    const completed = { ...(running as object), state: 'completed' } as never
    vi.mocked(requests.fetchGeneration)
      .mockReset()
      .mockResolvedValueOnce(running)
      .mockRejectedValueOnce(new ApiError({ code: 'internal_error', message: 'HTTP 502', status: 502 }))
      .mockResolvedValueOnce(completed)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
    renderHook(() => useGeneration('run_1'), { wrapper })
    const state = () => client.getQueryState<{ state: string }>(generationKeys.detail('run_1'))

    await vi.advanceTimersByTimeAsync(0)
    expect(state()?.data?.state).toBe('running')

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS)
    expect(state()?.status).toBe('error')
    expect(state()?.data?.state).toBe('running')

    await vi.advanceTimersByTimeAsync(RECONNECT_DELAYS_MS[0] ?? 0)
    expect(state()?.status).toBe('success')
    expect(state()?.data?.state).toBe('completed')
    expect(requests.fetchGeneration).toHaveBeenCalledTimes(3)

    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3)
    expect(requests.fetchGeneration).toHaveBeenCalledTimes(3)
  })
})
