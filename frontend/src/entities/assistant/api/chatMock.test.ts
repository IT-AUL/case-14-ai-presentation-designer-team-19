import { setupServer } from 'msw/node'
import { createMockBackend } from '@/shared/api/mocks/handlers'
import plan from '@/shared/api/mocks/fixtures/assistant/data-plan.json'
import project from '@/shared/api/mocks/fixtures/assistant/data-project.json'
import variants from '@/shared/api/mocks/fixtures/assistant/data-variants.json'
import { subscribeJobEvents } from './events'

const server = setupServer()
const projectId = project.body.id
const variantId = variants.body.items[0]?.id ?? ''
const base = `http://localhost:3000/api/v1/projects/${projectId}/agent`
let backend: ReturnType<typeof createMockBackend>
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
beforeEach(() => {
  sessionStorage.clear()
  backend = createMockBackend({ latency: 0, seed: false })
  server.use(...backend.handlers)
})
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
async function send(text: string, context = {}) {
  return fetch(`${base}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, ...context }),
  })
}
async function finish(id: string) {
  const events: { type: string; data: Record<string, unknown> }[] = []
  await subscribeJobEvents(id, { onEvent: (event) => events.push(event) })
  return events
}
it('stores the conversation, streams revisions and previews, and rejects concurrent sends', async () => {
  const response = await send('Сделай этот слайд короче', { variant_id: variantId })
  expect(response.status).toBe(202)
  const { job_id: jobId } = (await response.json()) as { job_id: string }
  const conflict = await send('Второе сообщение')
  expect(conflict.status).toBe(409)
  expect(await conflict.json()).toMatchObject({ error: { details: { job_id: jobId } } })
  const events = await finish(jobId)
  expect(events.map((event) => event.type)).toEqual([
    'agent.status',
    'agent.action',
    'agent.action',
    'slide.updated',
    'deck.revision',
    'agent.status',
    'agent.message',
    'job.completed',
  ])
  const update = events.find((event) => event.type === 'slide.updated')?.data
  const slides = (await fetch(`http://localhost:3000/api/v1/variants/${variantId}/slides`).then((response) =>
    response.json(),
  )) as { items: { id: string; revision: number; preview_artifact_id: string }[] }
  expect(slides.items.find((slide) => slide.id === update?.slide_id)).toMatchObject({
    revision: 2,
    preview_artifact_id: update?.preview_artifact_id,
  })
  const image = await fetch(`http://localhost:3000/api/v1/artifacts/${String(update?.preview_artifact_id)}/download`)
  expect(image.headers.get('Content-Type')).toContain('image/svg+xml')
  const thread = (await fetch(`${base}/thread`).then((response) => response.json())) as {
    items: unknown[]
    active_job_id: string | null
  }
  expect(thread.items).toHaveLength(2)
  expect(thread.active_job_id).toBeNull()
  server.resetHandlers(...createMockBackend({ latency: 0, seed: false }).handlers)
  const reloaded = (await fetch(`http://localhost:3000/api/v1/variants/${variantId}/slides`).then((response) =>
    response.json(),
  )) as { items: { preview_artifact_id: string }[] }
  expect(reloaded.items[0]?.preview_artifact_id).toBe(update?.preview_artifact_id)
  const undo = (await send('Отмени последнюю правку', { variant_id: variantId }).then((response) =>
    response.json(),
  )) as { job_id: string }
  await finish(undo.job_id)
  const restored = (await fetch(`http://localhost:3000/api/v1/variants/${variantId}/slides`).then((response) =>
    response.json(),
  )) as { items: { preview_artifact_id: string }[] }
  expect(restored.items[0]?.preview_artifact_id).not.toBe(update?.preview_artifact_id)
  expect((await fetch(`${base}/thread`, { method: 'DELETE' })).status).toBe(204)
  expect(await fetch(`${base}/thread`).then((response) => response.json())).toEqual({ items: [], active_job_id: null })
})
it('returns real question chips and web source steps in mock mode', async () => {
  const question = (await send('Что улучшить?').then((response) => response.json())) as { job_id: string }
  const questionEvents = await finish(question.job_id)
  expect(questionEvents.find((event) => event.type === 'agent.message')?.data.message).toMatchObject({
    question: { options: expect.arrayContaining(['Сделай этот слайд короче']) },
  })
  const research = (await send('Найди цифры в интернете', { plan_id: plan.body.plan_id }).then((response) =>
    response.json(),
  )) as { job_id: string }
  const events = await finish(research.job_id)
  expect(
    events.find(
      (event) => event.type === 'agent.action' && event.data.tool === 'web_research' && event.data.phase === 'finished',
    )?.data.sources,
  ).toHaveLength(1)
})
it('cancels an active run, preserves applied actions and persists the thread on reload', async () => {
  const accepted = (await send('Сделай короче').then((response) => response.json())) as { job_id: string }
  expect((await fetch(`${base}/jobs/${accepted.job_id}/cancel`, { method: 'POST' })).status).toBe(204)
  const events = await finish(accepted.job_id)
  expect(events.at(-1)?.data).toMatchObject({ state: 'canceled' })
  const saved = (await fetch(`${base}/thread`).then((response) => response.json())) as { items: unknown[] }
  server.resetHandlers(...createMockBackend({ latency: 0, seed: false }).handlers)
  expect(await fetch(`${base}/thread`).then((response) => response.json())).toMatchObject({
    items: saved.items,
    active_job_id: null,
  })
})
it('rejects empty text and the obsolete use_llm flag', async () => {
  expect((await send('')).status).toBe(422)
  expect((await send('Текст', { use_llm: false })).status).toBe(422)
})

it('paginates the current slide list with a reusable cursor', async () => {
  const url = `http://localhost:3000/api/v1/variants/${variantId}/slides`
  const first = (await fetch(`${url}?limit=1`).then((response) => response.json())) as {
    items: { id: string }[]
    next_cursor: string
  }
  const second = (await fetch(`${url}?limit=1&cursor=${first.next_cursor}`).then((response) => response.json())) as {
    items: { id: string }[]
  }
  expect(first.items).toHaveLength(1)
  expect(second.items).toHaveLength(1)
  expect(first.items[0]?.id).not.toBe(second.items[0]?.id)
  expect((await fetch(`${url}?cursor=invalid`)).status).toBe(422)
})
