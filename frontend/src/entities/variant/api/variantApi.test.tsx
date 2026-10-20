import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { api } from '@/shared/api'
import { useVariantSlides } from './variantApi'

afterEach(() => vi.restoreAllMocks())
it('reads every page of slides, sorts them and removes page duplicates', async () => {
  const get = vi.spyOn(api, 'GET').mockImplementation(((
    _path: string,
    init: { params: { query?: { cursor?: string } } },
  ) => {
    const next = init.params.query?.cursor
    return Promise.resolve({
      data: next
        ? {
            items: [
              { id: 's1', index: 1 },
              { id: 's2', index: 2 },
            ],
            next_cursor: null,
          }
        : { items: [{ id: 's2', index: 2 }], next_cursor: 'next' },
      response: new Response(),
    })
  }) as typeof api.GET)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  )
  const { result } = renderHook(() => useVariantSlides('v'), { wrapper })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.map((slide) => slide.id)).toEqual(['s1', 's2'])
  expect(get).toHaveBeenCalledTimes(2)
  expect(get.mock.calls[1]?.[1]).toMatchObject({ params: { query: { cursor: 'next' } } })
})
