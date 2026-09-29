import type { CreateExportResult } from '@/entities/passport'
import { resolveDeckFiles } from '@/entities/variant'
import { ApiError } from '@/shared/api'
import { variantFixtures } from '@/shared/api/mocks'
import { deriveExportState, type ExportStateInput } from './exportState'

const { variant, export: record } = variantFixtures.balanced
const files = resolveDeckFiles(variant, [record])
const idle = { status: 'idle' } as const

function input(overrides: Partial<ExportStateInput>): ExportStateInput {
  return { format: 'pdf', file: files.pdf, currentRevision: 1, supported: true, mutation: idle, ...overrides }
}

const notImplemented: CreateExportResult = {
  status: 'unavailable',
  formats: ['pdf'],
  message: 'not implemented',
  error: new ApiError({ code: 'not_implemented', message: 'not implemented', status: 501 }),
}

describe('deriveExportState', () => {
  it('is ready when the current revision already has the file', () => {
    expect(deriveExportState(input({}))).toMatchObject({ status: 'ready', stale: false, file: { artifactId: files.pdf?.artifactId, sizeBytes: 325015 } })
  })

  it('is idle when nothing has been exported yet', () => {
    expect(deriveExportState(input({ file: null }))).toEqual({ status: 'idle', file: null, stale: false, message: null })
  })

  it('is creating while the export request runs', () => {
    expect(deriveExportState(input({ file: null, mutation: { status: 'pending' } })).status).toBe('creating')
  })

  it('takes the fresh artifact from the created export record', () => {
    const created: CreateExportResult = { status: 'created', formats: ['pdf'], exportId: record.id, jobId: record.job_id, record: { ...record, deck_revision: 2 } }
    const state = deriveExportState(input({ currentRevision: 2, mutation: { status: 'success', data: created } }))

    expect(state).toMatchObject({ status: 'ready', stale: false, file: { deckRevision: 2 } })
  })

  it('waits for the variant to refresh when the record could not be loaded', () => {
    const created: CreateExportResult = { status: 'created', formats: ['pptx'], exportId: 'exp_x', jobId: 'job_x', record: null }
    expect(deriveExportState(input({ format: 'pptx', file: null, mutation: { status: 'success', data: created } })).status).toBe('creating')
  })

  it('marks files of an older revision as stale and offers a rebuild', () => {
    expect(deriveExportState(input({ currentRevision: 2 }))).toMatchObject({ status: 'idle', stale: true, file: { deckRevision: 1 } })
  })

  it('reports a rebuilt PDF that the backend refuses after repair honestly', () => {
    const state = deriveExportState(input({ currentRevision: 2, mutation: { status: 'success', data: notImplemented } }))

    expect(state).toMatchObject({ status: 'unavailable', stale: true, message: 'PDF относится к прошлой ревизии' })
    expect(state.file?.deckRevision).toBe(1)
  })

  it('marks HTML as not implemented by the service when capabilities do not list it', () => {
    expect(deriveExportState(input({ format: 'html', file: null, supported: false }))).toMatchObject({
      status: 'unavailable',
      message: 'Не реализовано сервисом',
    })
  })

  it('surfaces other failures as errors', () => {
    const error = new ApiError({ code: 'internal_error', message: 'Рендер упал', status: 500 })
    expect(deriveExportState(input({ file: null, mutation: { status: 'error', error } }))).toMatchObject({ status: 'error', message: 'Рендер упал' })
  })
})
