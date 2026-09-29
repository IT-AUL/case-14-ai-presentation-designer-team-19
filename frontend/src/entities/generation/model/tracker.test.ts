import generationFixture from '@/shared/api/mocks/fixtures/generation.json'
import { ApiError } from '@/shared/api'
import { submitGeneration, submitRetry } from '../api/requests'
import { elapsedSeconds } from '../lib/budget'
import { resolvePhase, resolveTiming } from './phase'
import { getTrackedGeneration, isTrackingId, retryGeneration, startGeneration, subscribeToGenerations, whenGenerationAccepted } from './tracker'
import type { GenerationAccepted, GenerationCreate, GenerationDetail } from './types'

vi.mock('../api/requests', () => ({
  submitGeneration: vi.fn(),
  submitRetry: vi.fn(),
}))

const recorded = generationFixture as GenerationDetail

const accepted: GenerationAccepted = {
  generation_id: recorded.id,
  job_id: recorded.job_id,
  variant_ids: recorded.variants.map((variant) => variant.id),
}

const body: GenerationCreate = {
  template_id: recorded.template_id,
  content_pack_id: recorded.content_pack_id,
  brief: { purpose: 'product', audience: 'руководство', language: 'ru', target_slide_count: 12 },
  variants: [{ strategy: 'faithful' }, { strategy: 'balanced' }, { strategy: 'visual' }],
  use_llm: false,
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined
  let reject: (reason: unknown) => void = () => undefined
  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })
  return { promise, resolve, reject }
}

const running: GenerationDetail = { ...recorded, state: 'running', finished_at: null }

describe('generation tracker', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.mocked(submitGeneration).mockReset()
    vi.mocked(submitRetry).mockReset()
  })

  it('returns a local tracking id before a synchronous backend answers', async () => {
    vi.useFakeTimers({ now: new Date('2026-09-25T14:04:43.900Z') })
    const request = deferred<GenerationAccepted>()
    vi.mocked(submitGeneration).mockReturnValue(request.promise)
    const listener = vi.fn()
    const unsubscribe = subscribeToGenerations(listener)

    const trackingId = startGeneration('prj_1', body)

    expect(isTrackingId(trackingId)).toBe(true)
    expect(submitGeneration).toHaveBeenCalledWith('prj_1', body)
    const pending = getTrackedGeneration(trackingId)
    expect(pending?.status).toBe('pending')
    expect(pending?.startedAt).toBe(Date.parse('2026-09-25T14:04:43.900Z'))
    expect(resolvePhase({ tracked: pending, trackingLost: false, generation: undefined, generationError: null })).toBe('submitting')

    vi.setSystemTime(new Date('2026-09-25T14:04:52.200Z'))
    request.resolve(accepted)
    await expect(whenGenerationAccepted(trackingId)).resolves.toEqual(accepted)
    await Promise.resolve()

    const settled = getTrackedGeneration(trackingId)
    expect(settled?.status).toBe('accepted')
    expect(settled?.accepted).toEqual(accepted)
    expect(getTrackedGeneration(recorded.id)).toBe(settled)
    expect(listener).toHaveBeenCalled()
    unsubscribe()

    expect(resolvePhase({ tracked: settled, trackingLost: false, generation: undefined, generationError: null })).toBe('running')
    expect(resolvePhase({ tracked: settled, trackingLost: false, generation: recorded, generationError: null })).toBe('completed')

    const timing = resolveTiming(settled, recorded)
    expect(elapsedSeconds(timing, Date.now())).toBeCloseTo(8.196, 2)
  })

  it('moves through running to completed when the backend accepts asynchronously', async () => {
    vi.mocked(submitGeneration).mockResolvedValue(accepted)
    const trackingId = startGeneration('prj_1', body)
    await whenGenerationAccepted(trackingId)
    await Promise.resolve()
    const tracked = getTrackedGeneration(trackingId)

    expect(resolvePhase({ tracked, trackingLost: false, generation: { ...running, state: 'queued' }, generationError: null })).toBe('running')
    expect(resolvePhase({ tracked, trackingLost: false, generation: running, generationError: null })).toBe('running')
    expect(resolveTiming(tracked, running).finishedAt).toBeNull()
    expect(resolvePhase({ tracked, trackingLost: false, generation: { ...recorded, state: 'canceled' }, generationError: null })).toBe('canceled')
  })

  it('fails when the submission is rejected', async () => {
    const error = new ApiError({ code: 'validation_error', message: 'bad brief', status: 422 })
    vi.mocked(submitGeneration).mockRejectedValue(error)
    const trackingId = startGeneration('prj_1', body)
    await expect(whenGenerationAccepted(trackingId)).rejects.toBe(error)
    await Promise.resolve()

    const tracked = getTrackedGeneration(trackingId)
    expect(tracked?.status).toBe('rejected')
    expect(tracked?.error).toBe(error)
    expect(resolvePhase({ tracked, trackingLost: false, generation: undefined, generationError: null })).toBe('failed')
    expect(resolveTiming(tracked, undefined).finishedAt).toBe(tracked?.settledAt)
  })

  it('captures synchronous throws from the request layer', async () => {
    vi.mocked(submitGeneration).mockImplementation(() => {
      throw new Error('boom')
    })
    const trackingId = startGeneration('prj_1', body)
    await expect(whenGenerationAccepted(trackingId)).rejects.toThrow('boom')
    await Promise.resolve()
    expect(getTrackedGeneration(trackingId)?.status).toBe('rejected')
  })

  it('tracks retries of an existing run', async () => {
    vi.mocked(submitRetry).mockResolvedValue({ ...accepted, generation_id: 'run_retry' })
    const trackingId = retryGeneration(recorded.id)
    expect(getTrackedGeneration(trackingId)?.parentGenerationId).toBe(recorded.id)
    await whenGenerationAccepted(trackingId)
    await Promise.resolve()
    expect(getTrackedGeneration('run_retry')?.kind).toBe('retry')
  })

  it('treats runs opened by generation id as server-timed', () => {
    expect(resolvePhase({ tracked: undefined, trackingLost: false, generation: undefined, generationError: null })).toBe('running')
    expect(resolvePhase({ tracked: undefined, trackingLost: true, generation: undefined, generationError: null })).toBe('failed')
    expect(resolvePhase({ tracked: undefined, trackingLost: false, generation: undefined, generationError: new Error('404') })).toBe('failed')
    const timing = resolveTiming(undefined, recorded)
    expect(timing.startedAt).toBe(Date.parse(recorded.created_at))
    expect(timing.finishedAt).toBe(Date.parse(recorded.finished_at ?? ''))
  })
})
