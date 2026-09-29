import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import { setupServer } from 'msw/node'
import { createMockBackend } from '@/shared/api/mocks'
import type * as AboutPanelModule from './AboutPanel'

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

let AboutPanel: typeof AboutPanelModule.AboutPanel

beforeAll(async () => {
  server.listen({ onUnhandledRequest: 'error' })
  ;({ AboutPanel } = await import('./AboutPanel'))
})
afterEach(() => server.resetHandlers())
afterAll(() => server.close())

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <AboutPanel open onOpenChange={() => undefined} />
    </QueryClientProvider>,
  )
  return screen.getByRole('dialog')
}

describe('AboutPanel', () => {
  it('shows versions, capabilities, the skill manifest and the audit rules', async () => {
    const dialog = renderPanel()

    expect(await within(dialog).findByText('deckdna-api')).toBeInTheDocument()

    const capabilities = within(dialog).getByRole('region', { name: 'Возможности сервиса' })
    const pptx = (await within(capabilities).findByText('Экспорт PPTX')).closest('li') as HTMLElement
    expect(within(pptx).getByText('есть')).toBeInTheDocument()
    expect(within(capabilities).queryByText('Экспорт HTML')).not.toBeInTheDocument()

    const manifest = within(dialog).getByRole('region', { name: 'Скилл, промпты и конфиги' })
    expect(await within(manifest).findByText('Инструменты')).toBeInTheDocument()
    expect(within(manifest).getByText('layout_compiler')).toBeInTheDocument()

    const rules = within(dialog).getByRole('region', { name: 'Правила аудита' })
    expect(within(rules).getByText(/24 детерминированных/)).toBeInTheDocument()
    expect(within(rules).getByText('template.font_family')).toBeInTheDocument()
    expect(within(rules).getAllByLabelText(/Детерминированная проверка/).length).toBeGreaterThanOrEqual(24)
  })
})
