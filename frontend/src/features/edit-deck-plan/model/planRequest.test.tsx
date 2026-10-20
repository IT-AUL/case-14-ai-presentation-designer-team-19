import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import type { ReactNode } from 'react'
import { createMockBackend } from '@/shared/api/mocks/handlers'
import planFixture from '@/shared/api/mocks/fixtures/assistant/data-plan.json'
import type { DeckPlan } from '@/entities/generation'
import type * as PlanRequestModule from './planRequest'

const server = setupServer()
const deckPlan = planFixture.body.plans[0]?.deck_plan as DeckPlan
const planId = planFixture.body.plan_id
const slideId = planFixture.body.plans[0]?.deck_plan.slides[1]?.id ?? ''

let mod: typeof PlanRequestModule

beforeAll(async () => {
  const NativeRequest = globalThis.Request
  class DocumentRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === 'string' ? new URL(input, window.location.origin) : input, init)
    }
  }
  vi.stubGlobal('Request', DocumentRequest)
  server.listen({ onUnhandledRequest: 'error' })
  mod = await import('./planRequest')
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

function stored(): PlanRequestModule.StoredPlanRequest {
  return {
    request: { template_id: 'tpl-1', content_pack_id: 'pack-1', brief: deckPlan.brief, strategies: ['balanced'], use_llm: false },
    result: { planId, proposals: [{ strategy: 'balanced', deckPlan }], sufficiency: null, research: null },
    keyMessage: 'Рост вдвое за счёт Москвы',
    createdAt: new Date().toISOString(),
  }
}

describe('useResearchPlanSlide', () => {
  beforeEach(() => {
    mod.clearSlideResearchOutcomes()
  })

  it('returns the researched slide and records the found numbers outcome', async () => {
    const { result } = setup(() => mod.useResearchPlanSlide(planId))
    await act(async () => {
      await result.current.mutateAsync({ slideId, focus: 'numbers' })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data?.added_units).toBe(2)
    expect(result.current.data?.slide.content_units.length).toBeGreaterThan(deckPlan.slides[1]?.content_units.length ?? 0)
    expect(result.current.data?.plan_set.plan_id).toBe(planId)
    expect(mod.getSlideResearchOutcome(slideId)?.message).toMatch(/^Нашёл и добавил: /)
  })

  it('records the empty outcome when the backend returns zero added units', async () => {
    server.use(
      http.post('*/api/v1/plans/:planId/slides/:slideId/research', () =>
        HttpResponse.json({
          slide: deckPlan.slides[1],
          added_units: 0,
          note: null,
          plan_set: planFixture.body,
        }),
      ),
    )
    const { result } = setup(() => mod.useResearchPlanSlide(planId))
    await act(async () => {
      await result.current.mutateAsync({ slideId, focus: 'numbers' })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(mod.getSlideResearchOutcome(slideId)?.message).toBe('Новых цифр в материалах не нашлось')
  })

  it('degrades quietly when the endpoint is missing and still records the empty outcome', async () => {
    server.use(http.all('*/api/v1/plans/:planId/slides/:slideId/research', () => new HttpResponse(null, { status: 404 })))
    const { result } = setup(() => mod.useResearchPlanSlide(planId))
    await act(async () => {
      await result.current.mutateAsync({ slideId, focus: 'numbers' })
    })
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.data).toBeNull()
    expect(mod.getSlideResearchOutcome(slideId)?.message).toBe('Новых цифр в материалах не нашлось')
  })
})

describe('plan request storage', () => {
  it('persists the edited deck plan and key message into the stored request', () => {
    const projectId = 'prj-storage'
    const before = mod.writePlanRequest(projectId, stored())
    const edited: DeckPlan = { ...deckPlan, brief: { ...deckPlan.brief, key_message: 'Новая мысль' } }
    const next = mod.syncPlanDraftToStorage(projectId, before, edited, 'balanced')
    expect(next.result.proposals[0]?.deckPlan.brief.key_message).toBe('Новая мысль')
    expect(next.keyMessage).toBe('Новая мысль')
    expect(next.request.brief.key_message).toBe('Новая мысль')
    expect(next.request.brief.target_slide_count).toBe(edited.slides.length)
    expect(mod.readPlanRequest(projectId)?.result.proposals[0]?.deckPlan.id).toBe(edited.id)
  })

  it('applies a server plan set and keeps the stored key message', () => {
    const projectId = 'prj-server'
    const before = mod.writePlanRequest(projectId, stored())
    const next = mod.applyServerPlanSet(projectId, before, planFixture.body)
    expect(next.result.planId).toBe(planId)
    expect(next.result.proposals).toHaveLength(1)
    expect(next.keyMessage).toBe('Рост вдвое за счёт Москвы')
  })

  it('fetches and stores an existing server plan without overwriting it', async () => {
    const projectId = 'prj-fetch'
    const fallback = stored().request
    const fetched = await mod.fetchAndStorePlan(projectId, planId, fallback)
    expect(fetched).not.toBeNull()
    expect(fetched?.result.planId).toBe(planId)
    expect(fetched?.result.proposals).toHaveLength(1)
    expect(mod.readPlanRequest(projectId)?.result.planId).toBe(planId)
  })
})
