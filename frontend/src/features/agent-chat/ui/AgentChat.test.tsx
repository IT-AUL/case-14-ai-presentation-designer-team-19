import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import type { AgentMessage, AgentThread } from '@/entities/assistant'
import type { AgentChatProps } from './AgentChat'
import type * as ChatModule from './AgentChat'
let AgentChat: typeof ChatModule.AgentChat

const server = setupServer()
let thread: AgentThread
let controller: ReadableStreamDefaultController<Uint8Array> | undefined
let requests: Record<string, unknown>[]
let sequence: number
let cancelCalls: number
const assistant = (patch: Partial<AgentMessage> = {}): AgentMessage => ({
  id: 'answer',
  role: 'assistant',
  text: 'Сократил до главного.',
  created_at: '2026-10-20T10:00:00Z',
  job_id: 'job',
  actions: [],
  mode: 'local',
  ...patch,
})

beforeAll(async () => {
  const NativeRequest = globalThis.Request
  class DocumentRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === 'string' ? new URL(input, window.location.origin) : input, init)
    }
  }
  vi.stubGlobal('Request', DocumentRequest)
  server.listen({ onUnhandledRequest: 'error' })
  ;({ AgentChat } = await import('./AgentChat'))
})
beforeEach(() => {
  sessionStorage.clear()
  controller = undefined
  thread = { items: [], active_job_id: null }
  requests = []
  sequence = 0
  cancelCalls = 0
  server.use(
    http.get('*/api/v1/projects/:id/agent/thread', () => HttpResponse.json(thread)),
    http.post('*/api/v1/projects/:id/agent/messages', async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>
      requests.push(body)
      thread.items.push({
        id: 'user',
        role: 'user',
        text: String(body.text),
        created_at: '2026-10-20T10:00:00Z',
        job_id: 'job',
      })
      thread.active_job_id = 'job'
      return HttpResponse.json(
        {
          job_id: 'job',
          message_id: 'user',
          events_url: '/api/v1/jobs/job/events',
          thread_url: '/api/v1/projects/p/agent/thread',
        },
        { status: 202 },
      )
    }),
    http.get(
      '*/api/v1/jobs/:id/events',
      () =>
        new HttpResponse(
          new ReadableStream<Uint8Array>({
            start(value) {
              controller = value
            },
          }),
          { headers: { 'Content-Type': 'text/event-stream' } },
        ),
    ),
    http.post('*/api/v1/projects/:id/agent/jobs/:job/cancel', () => {
      cancelCalls++
      thread.active_job_id = null
      return new HttpResponse(null, { status: 204 })
    }),
    http.delete('*/api/v1/projects/:id/agent/thread', () => {
      thread = { items: [], active_job_id: null }
      return new HttpResponse(null, { status: 204 })
    }),
  )
})
afterEach(() => server.resetHandlers())
afterAll(() => {
  server.close()
  vi.unstubAllGlobals()
})
function setup(props: Partial<AgentChatProps> = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity }, mutations: { retry: false } },
  })
  const node = (overrides: Partial<AgentChatProps> = {}) => (
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <AgentChat projectId="p" {...props} {...overrides} />
      </MemoryRouter>
    </QueryClientProvider>
  )
  return { client, node, ...render(node()) }
}
async function start(text = 'Сделай этот слайд короче') {
  const input = await screen.findByRole('textbox', { name: 'Сообщение помощнику' })
  await waitFor(() => expect(input).toBeEnabled())
  fireEvent.change(input, { target: { value: text } })
  fireEvent.keyDown(input, { key: 'Enter' })
  await waitFor(() => expect(controller).toBeDefined())
}
async function emit(type: string, data: Record<string, unknown>) {
  await act(async () => {
    if (type === 'agent.message') thread.items.push(data.message as AgentMessage)
    if (type === 'job.completed' || type === 'job.failed') thread.active_job_id = null
    controller?.enqueue(
      new TextEncoder().encode(
        `id: ${++sequence}\nevent: ${type}\ndata: ${JSON.stringify({ sequence, type, data })}\n\n`,
      ),
    )
    if (type.startsWith('job.')) controller?.close()
  })
}
it('sends the actual context without use_llm, streams ordered steps, and unlocks only on completion', async () => {
  setup({ variantId: 'v', slideId: 's', slides: [{ id: 's', index: 5 }] })
  await start()
  expect(requests[0]).toEqual({ text: 'Сделай этот слайд короче', variant_id: 'v', slide_id: 's' })
  expect(screen.getByRole('textbox')).toBeDisabled()
  await emit('agent.status', { text: 'Проверяю текст…', step: 1 })
  expect(await screen.findByRole('status')).toHaveTextContent('Проверяю текст…')
  await emit('agent.action', { phase: 'started', tool: 'rewrite_slide', slide: 5, text: 'Сокращаю текст' })
  expect(await screen.findByLabelText('Выполняю')).toBeInTheDocument()
  await emit('agent.action', {
    phase: 'finished',
    tool: 'rewrite_slide',
    slide: 5,
    ok: true,
    summary: 'Убрал повторы',
    revision: 2,
  })
  expect(await screen.findByLabelText('Выполнено')).toBeInTheDocument()
  expect(screen.getByText('Убрал повторы')).toBeInTheDocument()
  await emit('agent.message', { message: assistant() })
  expect(screen.queryByText('Сократил до главного.')).not.toBeInTheDocument()
  expect(screen.getByRole('textbox')).toBeDisabled()
  await emit('job.completed', { state: 'completed' })
  expect(await screen.findByText('Сократил до главного.')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole('textbox')).toBeEnabled())
  expect(screen.getByRole('textbox')).toHaveFocus()
})
it('restores the thread and sends question chips verbatim', async () => {
  thread.items = [
    assistant({ job_id: 'old', question: { text: 'Для кого?', options: ['Для команды', 'Для руководства'] } }),
  ]
  setup({ planId: 'plan' })
  fireEvent.click(await screen.findByRole('button', { name: 'Для команды' }))
  await waitFor(() => expect(requests[0]).toEqual({ text: 'Для команды', plan_id: 'plan' }))
})
it('resumes an active job from the server thread', async () => {
  thread.active_job_id = 'job'
  setup()
  await waitFor(() => expect(controller).toBeDefined())
  expect(screen.getByRole('textbox')).toBeDisabled()
  await emit('agent.status', { text: 'Продолжаю работу…' })
  expect(await screen.findByText('Продолжаю работу…')).toBeInTheDocument()
  await emit('job.completed', { state: 'completed' })
  await waitFor(() => expect(screen.getByRole('textbox')).toBeEnabled())
})
it('handles 409, attaches the existing job, and retains the unsent draft', async () => {
  server.use(
    http.post('*/api/v1/projects/:id/agent/messages', () => {
      thread.active_job_id = 'job'
      return HttpResponse.json(
        { error: { code: 'state_conflict', message: 'Busy', details: { job_id: 'job' } } },
        { status: 409 },
      )
    }),
  )
  setup()
  await start('Моя правка')
  expect(await screen.findByRole('alert')).toHaveTextContent('Ещё отвечаю на прошлое сообщение')
  expect(screen.getByRole('textbox')).toHaveValue('Моя правка')
  expect(screen.getByRole('textbox')).toBeDisabled()
})
it.each([404, 405])('silently returns the old command bar on thread %i', async (status) => {
  server.use(http.get('*/api/v1/projects/:id/agent/thread', () => new HttpResponse(null, { status })))
  setup({ fallback: <div>Старый бар</div> })
  expect(await screen.findByText('Старый бар')).toBeInTheDocument()
  expect(screen.queryByRole('alert')).not.toBeInTheDocument()
})
it('falls back when sending or streaming is unsupported', async () => {
  server.use(http.post('*/api/v1/projects/:id/agent/messages', () => new HttpResponse(null, { status: 404 })))
  setup({ fallback: <div>Старый бар</div> })
  const input = await screen.findByRole('textbox')
  await waitFor(() => expect(input).toBeEnabled())
  fireEvent.change(input, { target: { value: 'Текст' } })
  fireEvent.keyDown(input, { key: 'Enter' })
  expect(await screen.findByText('Старый бар')).toBeInTheDocument()
})
it('stops the response and starts a new conversation using server endpoints', async () => {
  setup()
  await start()
  fireEvent.click(screen.getByRole('button', { name: 'Остановить' }))
  await waitFor(() => expect(cancelCalls).toBe(1))
  await waitFor(() => expect(screen.getByRole('textbox')).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Новый разговор' }))
  expect(await screen.findByText('Обсудим презентацию?')).toBeInTheDocument()
  expect(thread.items).toHaveLength(0)
})
it('undoes only when the last assistant message contains a successful revision', async () => {
  thread.items = [
    assistant({
      job_id: 'old',
      actions: [{ tool: 'rewrite_slide', slide: 1, ok: true, summary: 'Сократил', revision: 2 }],
    }),
  ]
  setup()
  fireEvent.click(await screen.findByRole('button', { name: 'Отменить последнюю правку' }))
  await waitFor(() => expect(requests[0]?.text).toBe('Отмени последнюю правку'))
})
it('does not offer undo for unconfirmed or failed actions', async () => {
  thread.items = [
    assistant({
      job_id: 'old',
      actions: [{ tool: 'rewrite_slide', slide: 1, ok: false, summary: 'Не удалось', revision: 2 }],
    }),
  ]
  setup()
  await screen.findByText('Не удалось')
  expect(screen.queryByRole('button', { name: 'Отменить последнюю правку' })).not.toBeInTheDocument()
})
it('detaches slide context and changes only context when the selected slide changes', async () => {
  thread.items = [assistant({ job_id: 'old' })]
  const { node, rerender } = setup({
    variantId: 'v',
    slideId: 's1',
    slides: [
      { id: 's1', index: 1 },
      { id: 's2', index: 2 },
    ],
  })
  await screen.findByText('Сократил до главного.')
  fireEvent.click(screen.getByRole('button', { name: 'Снять привязку к слайду' }))
  await start()
  expect(requests[0]).not.toHaveProperty('slide_id')
  rerender(node({ slideId: 's2' }))
  expect(screen.getByText('Слайд 2 · Доводка')).toBeInTheDocument()
  expect(screen.getByText('Сократил до главного.')).toBeInTheDocument()
})
it('keeps Shift+Enter for multiline input and sends plain Enter', async () => {
  setup()
  const input = await screen.findByRole('textbox')
  await waitFor(() => expect(input).toBeEnabled())
  fireEvent.change(input, { target: { value: 'Две\nстроки' } })
  fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
  expect(requests).toHaveLength(0)
  fireEvent.keyDown(input, { key: 'Enter' })
  await waitFor(() => expect(requests[0]?.text).toBe('Две\nстроки'))
})
it('shows provider guidance, retries errors with original text, and hides unsafe source links', async () => {
  thread.items = [
    { id: 'u0', role: 'user', text: 'Исходный запрос', job_id: 'old', created_at: '' },
    assistant({
      id: 'a0',
      job_id: 'old',
      mode: 'unavailable',
      actions: [
        {
          tool: 'web_research',
          slide: 1,
          ok: true,
          summary: 'Нашёл источник',
          sources: [
            { title: 'Источник', url: 'https://example.com', publisher: 'Издание' },
            { title: 'Плохой URL', url: 'javascript:alert(1)' },
          ],
        },
      ],
    }),
    assistant({ job_id: 'old', mode: 'error', reply_to: 'u0' }),
  ]
  setup()
  expect(await screen.findByText(/Без подключённого провайдера/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Подключить провайдера' })).toHaveAttribute('href', '/?panel=provider')
  expect(screen.getByRole('link', { name: /Источник/ })).toHaveAttribute('href', 'https://example.com/')
  expect(screen.queryByRole('link', { name: /Плохой/ })).not.toBeInTheDocument()
  expect(within(screen.getByRole('log')).queryByText('web_research')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Повторить' }))
  await waitFor(() => expect(requests[0]?.text).toBe('Исходный запрос'))
})
it('notifies the plan only after successful completion, including a restored active job', async () => {
  thread.active_job_id = 'job'
  const onApplied = vi.fn()
  setup({ onApplied })
  await waitFor(() => expect(controller).toBeDefined())
  await emit('agent.message', {
    message: assistant({ actions: [{ tool: 'edit_plan', slide: null, ok: true, summary: 'Обновил', revision: 2 }] }),
  })
  expect(onApplied).not.toHaveBeenCalled()
  await emit('job.completed', { state: 'completed' })
  await waitFor(() => expect(onApplied).toHaveBeenCalledOnce())
})

it('offers slide actions only while attached to a slide and returns to deck suggestions when detached', async () => {
  setup({ variantId: 'v', slideId: 's', slides: [{ id: 's', index: 3 }] })
  expect(await screen.findByText('Работаю со слайдом 3. Выберите правку или напишите свою.')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Сделай этот слайд короче' })).toBeInTheDocument()
  expect(screen.getByRole('textbox')).toHaveAttribute('placeholder', 'Что изменить на этом слайде?')
  fireEvent.click(screen.getByRole('button', { name: 'Снять привязку к слайду' }))
  expect(screen.queryByRole('button', { name: 'Сделай этот слайд короче' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Добавь слайд про команду' })).toBeInTheDocument()
})
