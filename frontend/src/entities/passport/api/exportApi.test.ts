import fixtures from '@/shared/api/mocks/fixtures/variants.json'
import recordedPassport from '@/shared/api/mocks/fixtures/passport.json'
import type * as SharedApiModule from '@/shared/api'
import { createExport, fetchPassport } from './exportApi'

type SharedApi = typeof SharedApiModule

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  postMock: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
}))

vi.mock('@/shared/api', async (importOriginal) => {
  const actual = await importOriginal<SharedApi>()
  return { ...actual, api: { GET: getMock, POST: postMock } }
})

function respond(status: number, body: unknown) {
  const ok = status >= 200 && status < 300
  return Promise.resolve({ data: ok ? body : undefined, error: ok ? undefined : body, response: new Response(null, { status }) })
}

const exportRecord = fixtures.faithful.export
const variantId = exportRecord.variant_id

describe('export requests', () => {
  afterEach(() => {
    getMock.mockReset()
    postMock.mockReset()
  })

  it('creates an export and loads its record', async () => {
    postMock.mockReturnValueOnce(respond(202, { export_id: exportRecord.id, job_id: exportRecord.job_id }))
    getMock.mockReturnValueOnce(respond(200, exportRecord))

    const result = await createExport(variantId, ['pdf', 'quality_passport'])

    expect(result).toMatchObject({ status: 'created', exportId: exportRecord.id, jobId: exportRecord.job_id })
    expect(result.status === 'created' && result.record?.artifacts).toHaveLength(2)
  })

  it('reports formats the backend has not implemented instead of failing', async () => {
    const envelope = { error: { code: 'not_implemented', message: 'HTML export is not available', retryable: false, details: {} } }
    postMock.mockReturnValueOnce(respond(501, envelope))

    const result = await createExport(variantId, ['html'])

    expect(result).toMatchObject({ status: 'unavailable', formats: ['html'], message: 'HTML export is not available' })
  })

  it('rethrows other failures', async () => {
    const envelope = { error: { code: 'not_found', message: 'variant not found', retryable: false, details: {} } }
    postMock.mockReturnValueOnce(respond(404, envelope))

    await expect(createExport('var_missing', ['pdf'])).rejects.toMatchObject({ status: 404, code: 'not_found' })
  })

  it('downloads and parses the quality passport artifact', async () => {
    getMock.mockReturnValueOnce(respond(200, recordedPassport))

    const passport = await fetchPassport('art_170013f6c51f43bb8864c46ec04d383a')

    expect(getMock).toHaveBeenCalledWith('/api/v1/artifacts/{artifact_id}/download', { params: { path: { artifact_id: 'art_170013f6c51f43bb8864c46ec04d383a' } } })
    expect(passport.editability.peiLevel).toBe(3)
  })
})
