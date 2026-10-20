import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/shared/api'
import { useSlideChangeQueue } from './useSlideChangeQueue'

function ok<T>(data: T) {
  return Promise.resolve({ data, error: undefined, response: new Response(null, { status: 200 }) }) as never
}

function fail(status: number, code: string, message: string) {
  return Promise.resolve({
    data: undefined,
    error: { error: { code, message } },
    response: new Response(null, { status }),
  }) as never
}

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return { Wrapper, queryClient }
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('useSlideChangeQueue', () => {
  it('submits change with expected_revision = accepted_revision and cancels queued change', async () => {
    const getSpy = vi.spyOn(api, 'GET').mockImplementation((path: string) => {
      if (path === '/api/v1/variants/{variant_id}/changes') {
        return ok({
          variant_id: 'var_1',
          applied_revision: 1,
          accepted_revision: 2,
          items: [],
        })
      }
      return fail(404, 'not_found', path)
    })

    const postSpy = vi.spyOn(api, 'POST').mockImplementation((path: string, init?: unknown) => {
      const body = (init as { body?: { slide_id: string; expected_revision: number; action: string } } | undefined)?.body
      if (path === '/api/v1/variants/{variant_id}/changes') {
        return ok({
          change_id: 'chg_1',
          variant_id: 'var_1',
          slide_id: body?.slide_id ?? 'plan_s1',
          action: body?.action ?? 'shorter',
          state: 'queued',
          base_revision: body?.expected_revision ?? 2,
          target_revision: (body?.expected_revision ?? 2) + 1,
        })
      }
      if (path === '/api/v1/variants/{variant_id}/changes/{change_id}/cancel') {
        return ok({
          change_id: 'chg_1',
          variant_id: 'var_1',
          slide_id: 'plan_s1',
          action: 'shorter',
          state: 'cancelled',
          base_revision: 2,
          target_revision: 3,
        })
      }
      return fail(404, 'not_found', path)
    })

    const { Wrapper } = createWrapper()
    const { result } = renderHook(() => useSlideChangeQueue('var_1'), { wrapper: Wrapper })

    await waitFor(() => expect(getSpy).toHaveBeenCalledTimes(1))

    await act(async () => {
      await result.current.submitChange({ slideId: 'plan_s1', action: 'shorter' })
    })

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/variants/{variant_id}/changes',
      expect.objectContaining({
        body: expect.objectContaining({
          slide_id: 'plan_s1',
          expected_revision: 2,
          action: 'shorter',
        }),
      }),
    )
    expect(result.current.getSlideChange(['plan_s1'])?.state).toBe('queued')
    expect(result.current.hasPending).toBe(true)

    await act(async () => {
      await result.current.cancelChange('chg_1')
    })

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/variants/{variant_id}/changes/{change_id}/cancel',
      expect.objectContaining({
        params: { path: { variant_id: 'var_1', change_id: 'chg_1' } },
      }),
    )
    expect(result.current.getSlideChange(['plan_s1'])).toBeNull()
  })

  it('re-reads changes on 409 conflict without blind retry', async () => {
    let getCalls = 0
    const getSpy = vi.spyOn(api, 'GET').mockImplementation((path: string) => {
      if (path === '/api/v1/variants/{variant_id}/changes') {
        getCalls += 1
        return ok({
          variant_id: 'var_1',
          applied_revision: getCalls,
          accepted_revision: getCalls,
          items: [],
        })
      }
      return fail(404, 'not_found', path)
    })

    const postSpy = vi.spyOn(api, 'POST').mockImplementation((path: string) => {
      if (path === '/api/v1/variants/{variant_id}/changes') {
        return fail(409, 'revision_conflict', 'expected_revision=1 does not match current_revision=2')
      }
      return fail(404, 'not_found', path)
    })

    const { Wrapper } = createWrapper()
    const { result } = renderHook(() => useSlideChangeQueue('var_1'), { wrapper: Wrapper })

    await waitFor(() => expect(getSpy).toHaveBeenCalledTimes(1))

    await act(async () => {
      await result.current.submitChange({ slideId: 'plan_s1', action: 'punchier' })
    })

    expect(postSpy).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(getSpy.mock.calls.length).toBeGreaterThanOrEqual(2))
    expect(result.current.getSlideError(['plan_s1'])).toMatch(/перечитал очередь/i)
  })

  it('falls back to legacy regenerate endpoint on 404 or 501', async () => {
    vi.spyOn(api, 'GET').mockImplementation((path: string) => fail(404, 'not_found', path))
    const postSpy = vi.spyOn(api, 'POST').mockImplementation((path: string) => {
      if (path === '/api/v1/variants/{variant_id}/changes') {
        return fail(501, 'not_implemented', 'Queue not implemented')
      }
      if (path === '/api/v1/variants/{variant_id}/slides/{slide_id}/regenerate') {
        return ok({ assistant_note: 'Сделал слайд короче.' })
      }
      return fail(404, 'not_found', path)
    })

    const { Wrapper } = createWrapper()
    const { result } = renderHook(() => useSlideChangeQueue('var_1'), { wrapper: Wrapper })

    await act(async () => {
      await result.current.submitChange({
        slideId: 'plan_s1',
        fallbackSlideId: 'slide_1',
        action: 'shorter',
      })
    })

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/variants/{variant_id}/slides/{slide_id}/regenerate',
      expect.objectContaining({
        params: { path: { variant_id: 'var_1', slide_id: 'slide_1' } },
      }),
    )
    expect(result.current.getSlideNote(['plan_s1', 'slide_1'])).toBe('Сделал слайд короче.')
  })
})
