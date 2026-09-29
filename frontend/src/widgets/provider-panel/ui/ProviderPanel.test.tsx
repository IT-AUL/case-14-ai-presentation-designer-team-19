import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { setupServer } from 'msw/node'
import type * as ProviderSessionModule from '@/entities/provider-session'
import { createMockBackend } from '@/shared/api/mocks'
import type * as ProviderPanelModule from './ProviderPanel'

vi.hoisted(() => {
  const NativeRequest = globalThis.Request
  class DocumentRequest extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === 'string' ? new URL(input, window.location.origin) : input, init)
    }
  }
  globalThis.Request = DocumentRequest
})

const backend = createMockBackend({ latency: 0, generationDelay: 0, seed: false })
const server = setupServer(...backend.handlers)

let ProviderPanel: typeof ProviderPanelModule.ProviderPanel
let providerSession: typeof ProviderSessionModule

beforeAll(async () => {
  server.listen({ onUnhandledRequest: 'error' })
  ;({ ProviderPanel } = await import('./ProviderPanel'))
  providerSession = await import('@/entities/provider-session')
})
afterEach(() => {
  server.resetHandlers()
  providerSession.clearActiveProviderSession()
  window.sessionStorage.clear()
})
afterAll(() => server.close())

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <ProviderPanel open onOpenChange={() => undefined} />
    </QueryClientProvider>,
  )
  return screen.getByRole('dialog')
}

async function fillForm(dialog: HTMLElement, vision: string) {
  const user = userEvent.setup()
  await user.type(within(dialog).getByLabelText('Адрес API'), 'https://inference.example/v1')
  await user.type(within(dialog).getByLabelText('Модель для текста'), 'Qwen/Qwen3-32B')
  await user.type(within(dialog).getByLabelText('Модель для картинок слайдов'), vision)
  await user.type(within(dialog).getByLabelText('Токен'), 'very-secret-token')
  await user.click(within(dialog).getByRole('button', { name: 'Подключить и проверить' }))
  return user
}

describe('ProviderPanel', () => {
  it('shows the model rule and blocks models above 35B', async () => {
    const dialog = renderPanel()
    expect(within(dialog).getByText(/Apache-2.0 или MIT · до 35B/)).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Модель для текста')).toHaveAttribute('placeholder', 'Qwen/Qwen3-32B')
    expect(within(dialog).queryByText(/120b|72b/i)).not.toBeInTheDocument()

    await fillForm(dialog, 'Qwen/Qwen2.5-VL-72B-Instruct')

    expect(within(dialog).getByText(/Больше 35B параметров/)).toBeInTheDocument()
    expect(providerSession.getActiveProviderSession()).toBeNull()
  })

  it('connects, tests capabilities and never keeps the token', async () => {
    const dialog = renderPanel()
    const user = await fillForm(dialog, 'Qwen/Qwen2.5-VL-32B-Instruct')

    expect(await within(dialog).findByText('Подключено')).toBeInTheDocument()
    expect(await within(dialog).findByText('Структурированный ответ')).toBeInTheDocument()
    expect(within(dialog).getByText('Передан сервису · не показывается')).toBeInTheDocument()
    expect(providerSession.getActiveProviderSession()?.models.vision).toBe('Qwen/Qwen2.5-VL-32B-Instruct')
    const stored = [window.sessionStorage, window.localStorage].flatMap((storage) => Object.keys(storage).map((key) => storage.getItem(key) ?? ''))
    expect(stored.length).toBeGreaterThan(0)
    expect(stored.join('\n')).not.toContain('very-secret-token')
    expect(within(dialog).queryByDisplayValue('very-secret-token')).not.toBeInTheDocument()

    await user.click(within(dialog).getByRole('button', { name: 'Отключить' }))

    await waitFor(() => expect(providerSession.getActiveProviderSession()).toBeNull())
    expect(await within(dialog).findByRole('button', { name: 'Подключить и проверить' })).toBeInTheDocument()
  })
})
