import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { clearCommandHistoryCache, extractSlideNumber, resolveCommandSlideId } from '../model/commandModel'
import { AssistantCommandBar, type AssistantCommandBarProps } from './AssistantCommandBar'

const server = setupServer()

beforeAll(() => {
  const NativeRequest = globalThis.Request
  class DocumentRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === 'string' ? new URL(input, window.location.origin) : input, init)
    }
  }
  vi.stubGlobal('Request', DocumentRequest)
  server.listen({ onUnhandledRequest: 'bypass' })
})

beforeEach(() => {
  clearCommandHistoryCache()
  sessionStorage.clear()
})

afterEach(() => {
  server.resetHandlers()
})

afterAll(() => {
  server.close()
  vi.unstubAllGlobals()
})

function renderBar(props: Partial<AssistantCommandBarProps> = {}) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Infinity },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={client}>
      <AssistantCommandBar projectId="proj_test" {...props} />
    </QueryClientProvider>,
  )
}

describe('commandModel helpers', () => {
  it('extracts slide numbers from Russian phrases', () => {
    expect(extractSlideNumber('Например: короче на слайде 3')).toBe(3)
    expect(extractSlideNumber('убери слайд 5')).toBe(5)
    expect(extractSlideNumber('на 2-м слайде сделай таблицу')).toBe(2)
    expect(extractSlideNumber('аудитория руководство')).toBeNull()
  })

  it('resolves slide_plan_id from extracted slide number or falls back to active slideId', () => {
    const slides = [
      { id: 's-1', slide_plan_id: 'plan-1', index: 1 },
      { id: 's-2', slide_plan_id: 'plan-2', index: 2 },
      { id: 's-3', slide_plan_id: 'plan-3', index: 3 },
    ]
    expect(resolveCommandSlideId('короче на слайде 3', slides, 'plan-1')).toBe('plan-3')
    expect(resolveCommandSlideId('сделай короче', slides, 'plan-1')).toBe('plan-1')
  })
})

describe('AssistantCommandBar', () => {
  it('sends variant_id, resolved slide_id, and expected_revision on step 7 and shows accepted result', async () => {
    let capturedBody: Record<string, unknown> | null = null
    server.use(
      http.post('*/api/v1/projects/:projectId/commands', async ({ request }) => {
        capturedBody = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(
          {
            status: 'accepted',
            parser: 'rules',
            command: { intent: 'slide_change', action: 'shorter', text: String(capturedBody.text ?? '') },
            assistant_note: 'Принял: сделать слайд короче.',
            change: {
              id: 'chg_1',
              job_id: null,
              variant_id: 'var_1',
              slide_id: 'slide_3',
              expected_revision: 2,
              target_revision: 3,
              action: 'shorter',
              state: 'completed',
            },
          },
          { status: 202 },
        )
      }),
    )

    renderBar({
      variantId: 'var_1',
      slideId: 'slide_1',
      expectedRevision: 2,
      slides: [
        { id: 'slide_1', index: 0 },
        { id: 'slide_2', index: 1 },
        { id: 'slide_3', index: 2 },
      ],
    })

    const input = screen.getByPlaceholderText('Например: короче на слайде 3')
    fireEvent.change(input, { target: { value: 'короче на слайде 3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))

    await waitFor(() => {
      expect(screen.getByText('Принял: сделать слайд короче.')).toBeInTheDocument()
    })

    expect(capturedBody).toMatchObject({
      text: 'короче на слайде 3',
      variant_id: 'var_1',
      slide_id: 'slide_3',
      expected_revision: 2,
    })
  })

  it('shows clarification question and options, and submits the chosen option', async () => {
    let callCount = 0
    server.use(
      http.post('*/api/v1/projects/:projectId/commands', async ({ request }) => {
        callCount += 1
        const body = (await request.json()) as { text: string }
        if (callCount === 1) {
          return HttpResponse.json({
            status: 'clarification',
            parser: 'rules',
            command: { intent: 'clarification', text: body.text, question: 'Уточните, для кого презентация?' },
            assistant_note: 'Уточните, для кого презентация?',
            options: [
              { value: 'Аудитория — руководство', label: 'Для руководства' },
              { value: 'Аудитория — клиенты', label: 'Для клиентов' },
            ],
          })
        }
        return HttpResponse.json({
          status: 'applied',
          parser: 'rules',
          command: { intent: 'update_brief', action: 'update_brief', text: body.text },
          assistant_note: 'Принял: аудитория: руководство.',
        })
      }),
    )

    renderBar()

    const input = screen.getByPlaceholderText('Например: короче на слайде 3')
    fireEvent.change(input, { target: { value: 'поменяй тон' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))

    const optionButton = await screen.findByRole('button', { name: 'Для руководства' })
    expect(screen.getAllByText('Уточните, для кого презентация?').length).toBeGreaterThan(0)

    fireEvent.click(optionButton)

    await waitFor(() => {
      expect(screen.getByText('Принял: аудитория: руководство.')).toBeInTheDocument()
    })
    expect(screen.queryByRole('button', { name: 'Для руководства' })).not.toBeInTheDocument()
  })

  it('shows only the last 2 commands in history', async () => {
    server.use(
      http.post('*/api/v1/projects/:projectId/commands', async ({ request }) => {
        const body = (await request.json()) as { text: string }
        return HttpResponse.json({
          status: 'applied',
          parser: 'rules',
          command: { intent: 'update_brief', action: 'update_brief', text: body.text },
          assistant_note: `Принял: ${body.text}.`,
        })
      }),
    )

    renderBar()
    const input = screen.getByPlaceholderText('Например: короче на слайде 3')

    for (const cmd of ['Команда 1', 'Команда 2', 'Команда 3', 'Команда 4']) {
      fireEvent.change(input, { target: { value: cmd } })
      fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))
      await waitFor(() => {
        expect(screen.getByText(`Принял: ${cmd}.`)).toBeInTheDocument()
      })
    }

    const items = screen.getAllByRole('listitem')
    expect(items).toHaveLength(2)
    expect(screen.queryByText('Команда 2')).not.toBeInTheDocument()
    expect(screen.getByText('Команда 4')).toBeInTheDocument()
    expect(screen.getByText('Команда 3')).toBeInTheDocument()
  })

  it('hides the command bar when the endpoint returns 404', async () => {
    server.use(
      http.post('*/api/v1/projects/:projectId/commands', () => new HttpResponse(null, { status: 404 })),
    )

    renderBar()
    const input = screen.getByPlaceholderText('Например: короче на слайде 3')
    fireEvent.change(input, { target: { value: 'короче на слайде 3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Отправить' }))

    await waitFor(() => {
      expect(screen.queryByTestId('assistant-command-bar')).not.toBeInTheDocument()
    })
  })
})
