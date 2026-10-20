import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import type { ReactNode } from 'react'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMockBackend } from '@/shared/api/mocks/handlers'
import type * as AnalyzeBrandbookModule from './useAnalyzeBrandbook'

const server = setupServer()
let mod: typeof AnalyzeBrandbookModule

beforeAll(async () => {
  const NativeRequest = globalThis.Request
  class DocumentRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === 'string' ? new URL(input, window.location.origin) : input, init)
    }
  }
  vi.stubGlobal('Request', DocumentRequest)
  server.listen({ onUnhandledRequest: 'error' })
  mod = await import('./useAnalyzeBrandbook')
})

beforeEach(() => server.use(...createMockBackend({ latency: 0, seed: false }).handlers))
afterEach(() => server.resetHandlers())
afterAll(() => {
  server.close()
  vi.unstubAllGlobals()
})

function setup<T>(hook: () => T) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return renderHook(hook, { wrapper })
}

describe('useAnalyzeBrandbook', () => {
  it('calls PATCH /brandbooks/:id with analyze: true and returns colors and fonts with page sources', async () => {
    const { result } = setup(() => mod.useAnalyzeBrandbook())
    await act(async () => {
      await result.current.mutateAsync({ brandbookId: 'bb_5f2679e83440', priority: 'brandbook' })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.id).toBe('bb_5f2679e83440')
    expect(result.current.data?.palette.some((color) => typeof color.source === 'string' && color.source.includes('page='))).toBe(true)
    expect(result.current.data?.fonts.some((font) => typeof font.source === 'string' && font.source.includes('page='))).toBe(true)
  })

  it('degrades quietly when PATCH /brandbooks/:id returns 404/405/501', async () => {
    server.use(http.patch('*/api/v1/brandbooks/:brandbookId', () => new HttpResponse(null, { status: 501 })))
    const { result } = setup(() => mod.useAnalyzeBrandbook())
    await act(async () => {
      await result.current.mutateAsync({ brandbookId: 'bb_01j8northwind01' })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toBeNull()
  })

  it('retries without analyze when vision analysis fails', async () => {
    const bodies: unknown[] = []
    server.use(
      http.patch('*/api/v1/brandbooks/:brandbookId', async ({ request }) => {
        bodies.push(await request.json())
        if (bodies.length === 1) return HttpResponse.json({ error: { code: 'structured_output_invalid', message: 'analysis failed' } }, { status: 500 })
        return HttpResponse.json({ id: 'bb_retry', name: 'Брендбук', palette: [], fonts: [], priority: 'brandbook' })
      }),
    )
    const { result } = setup(() => mod.useAnalyzeBrandbook())
    await act(async () => {
      await result.current.mutateAsync({ brandbookId: 'bb_retry', priority: 'brandbook' })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(bodies).toHaveLength(2)
    expect(bodies[0]).toMatchObject({ analyze: true })
    expect(bodies[1]).toMatchObject({ analyze: false, priority: 'brandbook' })
    expect(result.current.data?.id).toBe('bb_retry')
  })
})
