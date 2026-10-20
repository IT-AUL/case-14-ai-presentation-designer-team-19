import { generationFixture } from '@/shared/api/mocks'
import type { DeckPlan } from '../model/types'
import { parsePlanDraft, requestPlans } from './plans'

const plan = generationFixture.deck_plan as DeckPlan

describe('parsePlanDraft', () => {
  it('reads the documented per-strategy response', () => {
    expect(parsePlanDraft({ plan_id: 'p1', plans: [{ strategy: 'balanced', deck_plan: plan }] })).toEqual({
      planId: 'p1',
      proposals: [{ strategy: 'balanced', deckPlan: plan }],
      sufficiency: null,
      research: null,
    })
  })

  it('reads sufficiency and research when the service reports them', () => {
    const result = parsePlanDraft({
      plan_id: 'p1',
      plans: [{ strategy: 'balanced', deck_plan: plan }],
      sufficiency: { status: 'short', content_slides: 4, target_slides: 10 },
      research: { queries: ['рынок мессенджеров'], sources: [{ id: 's1', title: 'Отчёт', url: 'https://www.example.com/a', facts_count: 3 }], duration_s: 12 },
    })
    expect(result.sufficiency).toEqual({ status: 'short', contentSlides: 4, targetSlides: 10 })
    expect(result.research).toEqual({
      queries: ['рынок мессенджеров'],
      sources: [{ id: 's1', title: 'Отчёт', url: 'https://www.example.com/a', site: 'example.com', accessedAt: null, facts: 3 }],
      seconds: 12,
    })
  })

  it('accepts a bare deck plan or a single deck_plan field', () => {
    expect(parsePlanDraft(plan).proposals).toHaveLength(1)
    expect(parsePlanDraft({ deck_plan: plan }).proposals[0]?.deckPlan).toBe(plan)
  })

  it('returns no proposals for an unrelated payload', () => {
    expect(parsePlanDraft({ plans: [{ strategy: 'x' }] }).proposals).toEqual([])
  })
})

describe('requestPlans', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    fetchMock.mockReset()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => vi.unstubAllGlobals())

  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })

  it('asks for an async plan and accepts a synchronous plan set', async () => {
    fetchMock.mockResolvedValueOnce(json({ plan_id: 'p1', plans: [{ strategy: 'balanced', deck_plan: plan }] }))
    const result = await requestPlans('prj', { content_pack_id: 'pack', brief: plan.brief, strategies: ['balanced'] })
    expect(result.planId).toBe('p1')
    const [, init] = fetchMock.mock.calls[0] ?? []
    expect(JSON.parse(String(init?.body))).toMatchObject({ respond_async: true, strategies: ['balanced'] })
  })

  it('follows an accepted plan job, reports progress and reads the finished plan', async () => {
    const events = 'id: 1\nevent: research.query\ndata: {"query":"рынок","stage":"research"}\n\n'
    fetchMock
      .mockResolvedValueOnce(json({ plan_id: 'p2', job_id: 'j1' }, 202))
      .mockResolvedValueOnce(new Response(events, { headers: { 'content-type': 'text/event-stream' } }))
      .mockResolvedValueOnce(json({ id: 'j1', state: 'completed', stage: 'outline', progress: 1 }))
      .mockResolvedValueOnce(json({ plan_id: 'p2', plans: [{ strategy: 'balanced', deck_plan: plan }] }))
    const onProgress = vi.fn()
    const result = await requestPlans('prj', { content_pack_id: 'pack', brief: plan.brief }, { onProgress, pollIntervalMs: 1 })
    expect(result.planId).toBe('p2')
    expect(onProgress).toHaveBeenCalledWith(expect.objectContaining({ queries: ['рынок'] }))
    expect(String(fetchMock.mock.calls[3]?.[0])).toContain('/projects/prj/plans/p2')
  })

  it('fails with the job error when the plan job fails', async () => {
    fetchMock
      .mockResolvedValueOnce(json({ plan_id: 'p3', job_id: 'j3' }, 202))
      .mockResolvedValueOnce(new Response('', { headers: { 'content-type': 'text/event-stream' } }))
      .mockResolvedValueOnce(json({ id: 'j3', state: 'failed', progress: 0, error: { code: 'research_failed', message: 'Поиск недоступен' } }))
    await expect(requestPlans('prj', { content_pack_id: 'pack', brief: plan.brief }, { pollIntervalMs: 1 })).rejects.toThrow('Поиск недоступен')
  })
})
