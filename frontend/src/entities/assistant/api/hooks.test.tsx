import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import type { ReactNode } from 'react'
import { createMockBackend } from '@/shared/api/mocks/handlers'
import project from '@/shared/api/mocks/fixtures/assistant/data-project.json'
import intake from '@/shared/api/mocks/fixtures/assistant/data-intake.json'
import understanding from '@/shared/api/mocks/fixtures/assistant/data-understanding.json'
import plan from '@/shared/api/mocks/fixtures/assistant/data-plan.json'
import styleStart from '@/shared/api/mocks/fixtures/assistant/data-style-start.json'
import preview from '@/shared/api/mocks/fixtures/assistant/data-style-preview.json'
import brand from '@/shared/api/mocks/fixtures/assistant/data-brandbook.json'
import slide from '@/shared/api/mocks/fixtures/assistant/data-slide.json'
import chart from '@/shared/api/mocks/fixtures/assistant/data-chart-slide.json'
import { assistantKeys } from './keys'
import type { IntakeInput } from './hooks'
import type * as HookTypes from './hooks'
import type * as EventTypes from './events'
import type * as ApiTypes from '@/shared/api'

let hooks: typeof HookTypes
let useJobEvents: typeof EventTypes.useJobEvents
let api: typeof ApiTypes.api
let unwrap: typeof ApiTypes.unwrap

const server = setupServer()
const projectId = project.body.id
const packId = understanding.body.content_pack_id
const planId = plan.body.plan_id
const slideId = plan.body.plans[0]?.deck_plan.slides[2]?.id ?? ''
const insightId = understanding.body.datasets[0]?.insights[0]?.id ?? ''
beforeAll(async () => {
  const NativeRequest = globalThis.Request
  class DocumentRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === 'string' ? new URL(input, window.location.origin) : input, init)
    }
  }
  vi.stubGlobal('Request', DocumentRequest)
  server.listen({ onUnhandledRequest: 'error' })
  ;({ api, unwrap } = await import('@/shared/api'))
  hooks = await import('./hooks')
  ;({ useJobEvents } = await import('./events'))
})
beforeEach(() => server.use(...createMockBackend({ latency: 0, seed: false }).handlers))
afterEach(() => server.resetHandlers())
afterAll(() => { server.close(); vi.unstubAllGlobals() })

function setup<T>(hook: () => T) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  return { client, ...renderHook(hook, { wrapper }) }
}

const queries: [string, () => { data: unknown; isSuccess: boolean }, (data: unknown) => void][] = [
  ['session', () => hooks.useAssistantSession(projectId), (data: unknown) => expect(data).toHaveProperty('step', 1)],
  ['template summary', () => hooks.useTemplateSummary(intake.body.template_id), (data: unknown) => expect(data).toHaveProperty('preview_artifact_ids', [])],
  ['brandbook', () => hooks.useBrandbook(brand.body.id), (data: unknown) => expect(data).toHaveProperty('name', brand.body.name)],
  ['brand summary', () => hooks.useBrandbookSummary(brand.body.id), (data: unknown) => expect(data).toHaveProperty('priority', 'brandbook')],
  ['understanding', () => hooks.useUnderstanding(packId, { useLlm: false }), (data: unknown) => expect(data).toHaveProperty('datasets.0.id', understanding.body.datasets[0]?.id)],
  ['questions', () => hooks.useQuestions(projectId), (data: unknown) => expect(data).toHaveProperty('questions.0.id', 'audience')],
  ['style preview', () => hooks.useStylePreview(preview.body.preview_id), (data: unknown) => expect(data).toHaveProperty('styles.2.strategy', 'visual')],
  ['slide explain', () => hooks.useSlideExplain(chart.body.id), (data: unknown) => expect(data).toHaveProperty('dataset.id')],
  ['slide', () => hooks.useSlide(slide.body.id), (data: unknown) => expect(data).toHaveProperty('id', slide.body.id)],
]

it.each(queries)('reads %s through MSW', async (_name, hook, verify) => {
  const { result } = setup(hook)
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  verify(result.current.data)
})

it('sends use_llm only when requested and marks the viewed session stale', async () => {
  let requested = ''
  server.use(http.get('*/api/v1/content-packs/:id/understanding', ({ request }) => { requested = new URL(request.url).search; return HttpResponse.json(understanding.body) }))
  const { result, client } = setup(() => hooks.useUnderstanding(packId, { useLlm: true }))
  const spy = vi.spyOn(client, 'invalidateQueries')
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(requested).toBe('?use_llm=true')
  expect(spy).toHaveBeenCalledWith({ queryKey: assistantKeys.sessions })
})

it('uploads multipart files and idea with progress and invalidates the project session', async () => {
  const { result, client } = setup(() => hooks.useIntake(projectId))
  const spy = vi.spyOn(client, 'invalidateQueries')
  const onProgress = vi.fn()
  const payload: IntakeInput = { files: [], idea: 'Итоги продаж', onProgress }
  await act(async () => { await result.current.mutateAsync(payload) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.files).toEqual([])
  expect(result.current.progress).toBe(1)
  expect(spy).toHaveBeenCalledWith({ queryKey: assistantKeys.session(projectId) })
})

it('starts autopilot and invalidates session', async () => {
  const { result, client } = setup(() => hooks.useStartAutopilot(projectId))
  const spy = vi.spyOn(client, 'invalidateQueries')
  await act(async () => { await result.current.mutateAsync({ from_step: 4, use_llm: false }) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.job_id).toMatch(/^job_/)
  expect(spy).toHaveBeenCalledWith({ queryKey: assistantKeys.session(projectId) })
})

it('changes intake file kind', async () => {
  const { result } = setup(() => hooks.useSetIntakeKind(projectId))
  await act(async () => { await result.current.mutateAsync({ name: 'purchases.csv', kind: 'content' }) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.files.find((file) => file.name === 'purchases.csv')?.kind).toBe('content')
})

it('changes brand priority and updates summary cache', async () => {
  const { result, client } = setup(() => hooks.useSetBrandPriority(brand.body.id))
  await act(async () => { await result.current.mutateAsync({ priority: 'template' }) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(client.getQueryData(assistantKeys.brandbookSummary(brand.body.id))).toHaveProperty('priority', 'template')
})

it('edits blocks into a new pack version and invalidates old and new packs', async () => {
  const { result, client } = setup(() => hooks.useEditBlocks(packId))
  const spy = vi.spyOn(client, 'invalidateQueries')
  const blockId = understanding.body.blocks[0]?.id ?? ''
  await act(async () => { await result.current.mutateAsync({ ops: [{ op: 'edit', block_id: blockId, title: 'Итоги' }] }) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.content_pack_id).not.toBe(packId)
  expect(result.current.data?.filled_from).toBe(packId)
  expect(result.current.data?.understanding?.blocks[0]?.title).toBe('Итоги')
  expect(spy).toHaveBeenCalledWith({ queryKey: ['content-pack', packId] })
  expect(spy).toHaveBeenCalledWith({ queryKey: ['content-pack', result.current.data?.content_pack_id] })
})

it('edits an insight and invalidates understanding and plans', async () => {
  const { result, client } = setup(() => hooks.useEditInsight(packId))
  const spy = vi.spyOn(client, 'invalidateQueries')
  await act(async () => { await result.current.mutateAsync({ insightId, confirmed: true }) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.confirmed).toBe(true)
  expect(spy).toHaveBeenCalledWith({ queryKey: assistantKeys.understanding(packId) })
  expect(spy).toHaveBeenCalledWith({ queryKey: ['plan'] })
})

it('adds author notes into a new pack', async () => {
  const { result } = setup(() => hooks.useAddNotes(packId))
  await act(async () => { await result.current.mutateAsync({ items: ['Проверить итоги'] }) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.understanding?.blocks.at(-1)?.text).toBe('Проверить итоги')
})

it('saves answers and invalidates questions and understanding', async () => {
  const { result, client } = setup(() => hooks.useSubmitAnswers(projectId))
  const spy = vi.spyOn(client, 'invalidateQueries')
  await act(async () => { await result.current.mutateAsync({ answers: { audience: 'совет директоров' } }) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.brief.audience).toBe('совет директоров')
  expect(spy).toHaveBeenCalledWith({ queryKey: assistantKeys.questions(projectId) })
  expect(spy).toHaveBeenCalledWith({ queryKey: ['assistant', 'understanding'] })
})

it('patches a plan chart and stores the returned plan', async () => {
  const chartSlide = plan.body.plans[0]?.deck_plan.slides.find((slide) => slide.chart?.type)
  expect(chartSlide).toBeDefined()
  const { result, client } = setup(() => hooks.usePatchPlanSlide(planId))
  await act(async () => { await result.current.mutateAsync({ slideId: chartSlide?.id ?? '', chart_type: 'table' }) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.plans[0]?.deck_plan.slides.find((slide) => slide.id === chartSlide?.id)?.chart).toHaveProperty('type', 'table')
  expect(client.getQueryData(assistantKeys.plan(planId))).toEqual(result.current.data)
})

it('performs a quick plan action', async () => {
  const { result } = setup(() => hooks.usePlanSlideAction(planId))
  await act(async () => { await result.current.mutateAsync({ slideId, action: 'to_diagram' }) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.plans[0]?.deck_plan.slides.find((slide) => slide.id === slideId)?.desired_visual).toBe('diagram')
})

it('starts asynchronous style previews', async () => {
  const { result } = setup(() => hooks.useStartStylePreviews(planId))
  await act(async () => { await result.current.mutateAsync({}) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.preview_id).toBe(preview.body.preview_id)
})

it('regenerates a single slide and invalidates artifacts and audit', async () => {
  const { result, client } = setup(() => hooks.useRegenerateSlide(slide.body.variant_id))
  const spy = vi.spyOn(client, 'invalidateQueries')
  await act(async () => { await result.current.mutateAsync({ slideId: slide.body.id, action: 'shorter' }) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.deck_revision).toBe(2)
  expect(client.getQueryData(assistantKeys.slide(slide.body.id))).toHaveProperty('id', slide.body.id)
  expect(spy).toHaveBeenCalledWith({ queryKey: ['audit'] })
  expect(spy).toHaveBeenCalledWith({ queryKey: ['export'] })
})

it('reads recorded job events and settles at the terminal event', async () => {
  const { result } = setup(() => useJobEvents(styleStart.body.job_id))
  await waitFor(() => expect(result.current.done).toBe(true))
  expect(result.current.lastEvent?.type).toBe('job.completed')
  expect(result.current.events.length).toBeGreaterThan(3)
  expect(result.current.error).toBeNull()
})

it.each([404, 405, 501])('returns an absent field on HTTP %i for queries and mutations', async (status) => {
  server.use(http.all('*/api/v1/projects/:id/questions', () => new HttpResponse(null, { status })), http.all('*/api/v1/projects/:id/answers', () => new HttpResponse(null, { status })))
  const query = setup(() => hooks.useQuestions(projectId))
  const mutation = setup(() => hooks.useSubmitAnswers(projectId))
  await waitFor(() => expect(query.result.current.isSuccess).toBe(true))
  await act(async () => { await mutation.result.current.mutateAsync({ answers: {} }) })
  await waitFor(() => expect(mutation.result.current.isSuccess).toBe(true))
  expect(query.result.current.data).toBeNull()
  expect(mutation.result.current.data).toBeNull()
})

it('keeps validation errors visible', async () => {
  server.use(http.post('*/api/v1/projects/:id/answers', () => HttpResponse.json({ error: { code: 'invalid_input', message: 'Проверьте ответ' } }, { status: 422 })))
  const { result } = setup(() => hooks.useSubmitAnswers(projectId))
  await act(async () => { await expect(result.current.mutateAsync({ answers: {} })).rejects.toHaveProperty('status', 422) })
  await waitFor(() => expect(result.current.error?.message).toBe('Проверьте ответ'))
})

it('keeps missing identifiers disabled', () => {
  const { result } = setup(() => [hooks.useAssistantSession(null), hooks.useTemplateSummary(undefined), hooks.useBrandbook(null), hooks.useBrandbookSummary(''), hooks.useUnderstanding(null), hooks.useQuestions(null), hooks.useStylePreview(null), hooks.useSlideExplain(null), hooks.useSlide(null)])
  expect(result.current.every((query) => query.fetchStatus === 'idle')).toBe(true)
})

it('lets the existing project endpoint create an isolated project for intake', async () => {
  const created = await unwrap(api.POST('/api/v1/projects', { body: { name: 'Проверка', default_language: 'ru', target_slide_count: 5 } }))
  const { result } = setup(() => hooks.useIntake(created.id))
  await act(async () => { await result.current.mutateAsync({ files: [], idea: 'Итоги года' }) })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.project_id).toBe(created.id)
})

it('rejects intake before sending an already canceled upload', async () => {
  const controller = new AbortController()
  controller.abort()
  const request = vi.fn()
  server.use(http.post('*/api/v1/projects/:id/intake', () => { request(); return HttpResponse.json(intake.body) }))
  const { result } = setup(() => hooks.useIntake(projectId))
  await act(async () => { await expect(result.current.mutateAsync({ files: [], idea: 'Итоги', signal: controller.signal })).rejects.toHaveProperty('name', 'AbortError') })
  expect(request).not.toHaveBeenCalled()
})

it('clears upload progress when reset', async () => {
  const { result } = setup(() => hooks.useIntake(projectId))
  await act(async () => { await result.current.mutateAsync({ files: [], idea: 'Итоги' }) })
  await waitFor(() => expect(result.current.progress).toBe(1))
  act(() => result.current.reset())
  expect(result.current.progress).toBe(0)
  expect(result.current.isIdle).toBe(true)
})

it('keeps cached events and avoids re-opening a completed subscription', async () => {
  const { result, rerender } = setup(() => useJobEvents(styleStart.body.job_id))
  await waitFor(() => expect(result.current.done).toBe(true))
  const count = result.current.events.length
  rerender()
  expect(result.current.events).toHaveLength(count)
})

it('serves recorded PNG artifacts without reaching the backend', async () => {
  const id = preview.body.styles[0]?.previews[0]?.image_artifact_id
  const response = await fetch(`http://localhost/api/v1/artifacts/${id}/download`)
  expect(response.headers.get('content-type')).toContain('image/png')
  expect([...new Uint8Array(await response.arrayBuffer()).slice(0, 8)]).toEqual([137, 80, 78, 71, 13, 10, 26, 10])
})

it('runs intake, understanding, answers, plan, style, events and assembly offline', async () => {
  async function request(path: string, body?: unknown) {
    const response = await fetch(`http://localhost/api/v1${path}`, body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : undefined)
    expect(response.ok).toBe(true)
    return await response.json() as Record<string, unknown>
  }
  const created = await request('/projects', { name: 'Шаги', default_language: 'ru', target_slide_count: 5 })
  const uploaded = setup(() => hooks.useIntake(String(created.id)))
  await act(async () => { await uploaded.result.current.mutateAsync({ files: [], idea: 'Итоги продаж' }) })
  await waitFor(() => expect(uploaded.result.current.isSuccess).toBe(true))
  const accepted = uploaded.result.current.data
  expect(accepted).not.toBeNull()
  await request(`/content-packs/${accepted?.content_pack_id}/understanding`)
  const answered = await request(`/projects/${created.id}/answers`, { answers: {} })
  const planned = await request(`/projects/${created.id}/plans`, { content_pack_id: accepted?.content_pack_id, template_id: accepted?.template_id, brief: answered.brief })
  const started = await request(`/plans/${planned.plan_id}/style-previews`, { respond_async: true })
  const events = setup(() => useJobEvents(String(started.job_id)))
  await waitFor(() => expect(events.result.current.done).toBe(true))
  const generated = await request(`/projects/${created.id}/generations`, { preview_id: started.preview_id })
  const generationEvents = setup(() => useJobEvents(String(generated.job_id)))
  await waitFor(() => expect(generationEvents.result.current.done).toBe(true))
  const resumed = await request(`/projects/${created.id}/session`)
  expect(resumed.step).toBe(7)
  const variants = await request(`/generations/${generated.generation_id}/variants`)
  expect(variants.items).toHaveLength(1)
})

it('resumes a subscription after unmount from the last delivered sequence', async () => {
  let connections = 0
  const cursors: string[] = []
  server.use(http.get('*/api/v1/jobs/reconnect/events', ({ request }) => {
    connections++
    cursors.push(request.headers.get('Last-Event-ID') ?? '')
    if (connections === 1) return new HttpResponse(new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('id: 1\nevent: stage.started\ndata: {"sequence":1,"event":"stage.started"}\n\n')) } }), { headers: { 'Content-Type': 'text/event-stream' } })
    return new HttpResponse('id: 2\nevent: job.completed\ndata: {"sequence":2,"event":"job.completed"}\n\n', { headers: { 'Content-Type': 'text/event-stream' } })
  }))
  const first = setup(() => useJobEvents('reconnect'))
  await waitFor(() => expect(first.result.current.lastEvent?.sequence).toBe(1))
  first.unmount()
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={first.client}>{children}</QueryClientProvider>
  const second = renderHook(() => useJobEvents('reconnect'), { wrapper })
  await waitFor(() => expect(second.result.current.done).toBe(true))
  expect(cursors).toEqual(['0', '1'])
  expect(second.result.current.events.map((event) => event.sequence)).toEqual([1, 2])
})

it('passes files to the upload helper and reports intermediate progress', async () => {
  const original = globalThis.XMLHttpRequest
  let postedForm: FormData | undefined
  let finish: (() => void) | undefined
  class UploadRequest {
    upload: { onprogress?: (event: { lengthComputable: boolean; loaded: number; total: number }) => void } = {}
    onload?: () => void
    status = 202
    response = intake.body
    open = vi.fn()
    abort = vi.fn()
    send(form: FormData) {
      postedForm = form
      this.upload.onprogress?.({ lengthComputable: true, loaded: 1, total: 2 })
      finish = () => this.onload?.()
    }
  }
  vi.stubGlobal('XMLHttpRequest', UploadRequest)
  try {
    const onProgress = vi.fn()
    const { result } = setup(() => hooks.useIntake(projectId))
    act(() => result.current.mutate({ files: [new File(['slides'], 'template.pptx')], idea: 'Итоги года', onProgress }))
    await waitFor(() => expect(result.current.progress).toBe(0.5))
    expect(onProgress).toHaveBeenCalledWith(0.5)
    expect(postedForm?.get('idea')).toBe('Итоги года')
    expect(postedForm?.getAll('files')).toHaveLength(1)
    act(() => finish?.())
    await waitFor(() => expect(result.current.isSuccess).toBe(true))
    expect(result.current.progress).toBe(1)
  } finally {
    globalThis.XMLHttpRequest = original
  }
})

it('sends assistant command, normalizes accepted slide change, and tracks change job events', async () => {
  const { result, client } = setup(() => hooks.useAssistantCommand(projectId))
  const spy = vi.spyOn(client, 'invalidateQueries')
  await act(async () => {
    await result.current.mutateAsync({
      text: 'Сделай короче',
      variant_id: slide.body.variant_id,
      slide_id: slideId,
      expected_revision: 1,
    })
  })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.status).toBe('accepted')
  expect(result.current.data?.assistant_note).toContain('Принял:')
  expect(result.current.data?.change?.job_id).toMatch(/^job_chg/)
  expect(spy).toHaveBeenCalledWith({ queryKey: assistantKeys.session(projectId) })
  expect(spy).toHaveBeenCalledWith({ queryKey: ['audit'] })

  const jobId = result.current.data?.change?.job_id ?? ''
  const events = setup(() => useJobEvents(jobId))
  await waitFor(() => expect(events.result.current.done).toBe(true))
  expect(events.result.current.lastEvent?.type).toBe('job.completed')
})

it('returns clarification with options without invalidating step queries', async () => {
  const { result, client } = setup(() => hooks.useAssistantCommand(projectId))
  const spy = vi.spyOn(client, 'invalidateQueries')
  await act(async () => {
    await result.current.mutateAsync({ text: 'Поменяй что-нибудь' })
  })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data?.status).toBe('clarification')
  expect(result.current.data?.options.length).toBeGreaterThan(0)
  expect(spy).not.toHaveBeenCalled()
})

it.each([404, 405, 501])('returns null when assistant command endpoint responds with %i', async (status) => {
  server.use(http.post('*/api/v1/projects/:id/commands', () => new HttpResponse(null, { status })))
  const { result } = setup(() => hooks.useAssistantCommand(projectId))
  await act(async () => {
    await result.current.mutateAsync({ text: 'Короче на слайде 3' })
  })
  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(result.current.data).toBeNull()
})

