import { requestJson, requestOptionalJson } from './raw'

function respond(status: number, body: unknown, type = 'application/json') {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(typeof body === 'string' ? body : JSON.stringify(body), { status, headers: { 'content-type': type } }))
}

afterEach(() => vi.restoreAllMocks())

describe('raw api client', () => {
  it('builds the url with the api base and query', async () => {
    const fetch = respond(200, { ok: true })
    await requestJson('/audits/a/repairs', { query: { dry_run: true, skip: null }, body: { x: 1 } })
    expect(fetch).toHaveBeenCalledWith('/api/v1/audits/a/repairs?dry_run=true', expect.objectContaining({ method: 'POST', body: '{"x":1}' }))
  })

  it('treats a missing endpoint as unsupported', async () => {
    respond(404, { error: { code: 'not_found', message: 'no route' } })
    await expect(requestOptionalJson('/audit/rules')).resolves.toBeNull()
  })

  it('treats an html fallback page as unsupported instead of data', async () => {
    respond(200, '<!doctype html>', 'text/html')
    await expect(requestJson('/audit/rules')).rejects.toMatchObject({ status: 501 })
  })

  it('surfaces real service errors', async () => {
    respond(500, { error: { code: 'internal_error', message: 'boom' } })
    await expect(requestOptionalJson('/audit/rules')).rejects.toMatchObject({ code: 'internal_error' })
  })
})
