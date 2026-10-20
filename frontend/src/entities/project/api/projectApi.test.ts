import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from '@/shared/api'
import { fetchAllProjects } from './projectApi'

function page(items: { id: string }[], next: string | null) {
  return Promise.resolve({ data: { items, next_cursor: next }, error: undefined, response: new Response(null, { status: 200 }) })
}

afterEach(() => vi.restoreAllMocks())

describe('fetchAllProjects', () => {
  it('follows the cursor until the last page so newer projects are not lost', async () => {
    const get = vi
      .spyOn(api, 'GET')
      .mockImplementationOnce((() => page([{ id: 'old-1' }, { id: 'old-2' }], 'c1')) as never)
      .mockImplementationOnce((() => page([{ id: 'new-1' }], null)) as never)

    const projects = await fetchAllProjects()

    expect(projects.map((project) => project.id)).toEqual(['old-1', 'old-2', 'new-1'])
    expect(get).toHaveBeenCalledTimes(2)
    expect(get.mock.calls[1]?.[1]).toEqual({ params: { query: { cursor: 'c1' } } })
  })
})
