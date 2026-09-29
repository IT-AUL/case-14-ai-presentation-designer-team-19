import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { api, artifactUrl } from '@/shared/api'
import { generationFixture as generation, passportFixture as passport, variantFixtures as fixtures, type VariantFixture } from '@/shared/api/mocks'
import { routes } from '@/shared/config'
import { ToastProvider } from '@/shared/ui'
import { VariantsPage } from './VariantsPage'

vi.mock('@/shared/ui', async (importOriginal) => {
  const original = await importOriginal<Record<string, unknown>>()
  return {
    ...original,
    PdfPage: ({ url, pageNumber, label, children }: { url: string | null; pageNumber: number; label?: string; children?: ReactNode }) => (
      <div data-testid="pdf-page" data-url={url ?? ''} data-page={pageNumber} aria-label={label}>
        {children}
      </div>
    ),
  }
})

type Strategy = keyof typeof fixtures
type Fixture = VariantFixture

const PROJECT_ID = generation.project_id
const RUN_ID = generation.id

interface FakeInit {
  params?: { path?: Record<string, string> }
  body?: unknown
}

function clone<T>(value: T): T {
  return structuredClone(value)
}

function ok(data: unknown) {
  return Promise.resolve({ data, error: undefined, response: new Response(null, { status: 200 }) })
}

function fail(status: number, code: string, message: string) {
  return Promise.resolve({
    data: undefined,
    error: { error: { code, message, stage: null, retryable: false, request_id: 'req_test' } },
    response: new Response(null, { status }),
  })
}

let decks: Record<Strategy, Fixture>
let variantsFailure: string | null
let passportPerDeck: boolean

function deckById(variantId: string): Fixture | undefined {
  return Object.values(decks).find((deck) => deck.variant.id === variantId)
}

function passportFor(artifactId: string) {
  const deck = Object.values(decks).find((item) => item.export.artifacts.some((artifact) => artifact.artifact_id === artifactId))
  if (!passportPerDeck || !deck) return passport
  const count = (severity: string) => deck.issues.filter((issue) => issue.severity === severity).length
  return {
    ...passport,
    issues_summary: { blocker: count('blocker'), error: count('error'), warning: count('warning'), info: count('info'), unresolved: deck.issues.length },
  }
}

function fakeGet(path: string, init?: FakeInit) {
  const params = init?.params?.path ?? {}
  switch (path) {
    case '/api/v1/generations/{run_id}/variants':
      if (variantsFailure) return fail(404, 'not_found', variantsFailure)
      return ok({ items: Object.values(decks).map((deck) => deck.variant) })
    case '/api/v1/variants/{variant_id}':
      return ok(deckById(params.variant_id ?? '')?.variant)
    case '/api/v1/variants/{variant_id}/slides':
      return ok({ items: deckById(params.variant_id ?? '')?.slides ?? [] })
    case '/api/v1/exports/{export_id}':
      return ok(Object.values(decks).find((deck) => deck.export.id === params.export_id)?.export)
    case '/api/v1/artifacts/{artifact_id}/download':
      return ok(passportFor(params.artifact_id ?? ''))
    default:
      return fail(404, 'not_found', `unexpected ${path}`)
  }
}

function renderPage(search = '') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const router = createMemoryRouter(
    [
      { path: '/projects/:projectId/runs/:runId/variants', element: <VariantsPage /> },
      { path: '*', element: <div>other page</div> },
    ],
    { initialEntries: [`${routes.variants(PROJECT_ID, RUN_ID)}${search}`] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>,
  )
  return router
}

async function cards() {
  const headings = await screen.findAllByRole('heading', { level: 2 })
  return headings.map((heading) => heading.closest('section') as HTMLElement)
}

let postSpy: MockInstance

beforeEach(() => {
  decks = clone(fixtures)
  variantsFailure = null
  passportPerDeck = false
  vi.spyOn(api, 'GET').mockImplementation(fakeGet as unknown as typeof api.GET)
  postSpy = vi.spyOn(api, 'POST').mockImplementation((() => fail(501, 'not_implemented', 'PDF для ревизии не поддерживается')) as unknown as typeof api.POST) as unknown as MockInstance
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('VariantsPage', () => {
  it('explains the axis and shows three strategy cards in catalog order', async () => {
    renderPage()
    expect(screen.getByText(/разный баланс текста и визуализации/)).toBeInTheDocument()

    const [faithful, balanced, visual] = await cards()
    expect(within(faithful as HTMLElement).getByRole('heading')).toHaveTextContent('Близко к шаблону')
    expect(within(balanced as HTMLElement).getByRole('heading')).toHaveTextContent('Сбалансированный')
    expect(within(visual as HTMLElement).getByRole('heading')).toHaveTextContent('Визуальный')
    expect(within(balanced as HTMLElement).getByText('по умолчанию')).toBeInTheDocument()
    expect(within(faithful as HTMLElement).queryByText('по умолчанию')).not.toBeInTheDocument()
  })

  it('shows real metrics and the severity breakdown only when the passport matches', async () => {
    renderPage()
    const [faithful, balanced] = await cards()
    const faithfulCard = within(faithful as HTMLElement)
    const balancedCard = within(balanced as HTMLElement)

    expect(faithfulCard.getByText(/без ошибок/)).toBeInTheDocument()
    expect(faithfulCard.getByText('3/5')).toBeInTheDocument()
    expect(faithfulCard.getByText('92 проблемы')).toBeInTheDocument()
    expect(await balancedCard.findByText('Критичных:', { exact: false })).toHaveTextContent('Критичных: 54')
    expect(balancedCard.getByText('108 проблем: 54 ошибки, 54 предупреждения')).toBeInTheDocument()
    expect(balancedCard.getByText('без растровых слайдов')).toBeInTheDocument()
    expect(faithfulCard.queryByText(/Критичных/)).not.toBeInTheDocument()
    expect(faithfulCard.queryByText('Соответствие')).not.toBeInTheDocument()
  })

  it('recommends the variant with the fewest critical issues once every passport is in', async () => {
    passportPerDeck = true
    renderPage()
    const [faithful, balanced, visual] = await cards()
    const visualCard = within(visual as HTMLElement)

    expect(await visualCard.findByText('рекомендуем')).toBeInTheDocument()
    expect(visualCard.getByText('Меньше всего критичных проблем — 36, у других от 46.')).toBeInTheDocument()
    expect(visualCard.getByText('Критичных:', { exact: false })).toHaveTextContent('Критичных: 36')
    expect(within(balanced as HTMLElement).queryByText('по умолчанию')).not.toBeInTheDocument()
    expect(within(faithful as HTMLElement).queryByText('рекомендуем')).not.toBeInTheDocument()
  })

  it('renders PDF thumbnails that open the slide in the audit', async () => {
    renderPage()
    const [faithful] = await cards()
    const thumbs = await within(faithful as HTMLElement).findAllByTestId('pdf-page')
    expect(thumbs).toHaveLength(decks.faithful.slides.length)
    const pdfArtifact = decks.faithful.export.artifacts.find((artifact) => artifact.format === 'pdf')
    expect(thumbs[0]).toHaveAttribute('data-url', artifactUrl(pdfArtifact?.artifact_id ?? ''))
    expect(thumbs[2]).toHaveAttribute('data-page', '3')

    const link = thumbs[2]?.closest('a')
    expect(link).toHaveAttribute('href', `${routes.audit(PROJECT_ID, RUN_ID, decks.faithful.variant.id)}?slide=3`)
  })

  it('links actions to the audit, the PDF and the PPTX download', async () => {
    renderPage()
    const [faithful] = await cards()
    const card = within(faithful as HTMLElement)
    expect(card.getByRole('link', { name: 'Открыть и проверить' })).toHaveAttribute('href', routes.audit(PROJECT_ID, RUN_ID, decks.faithful.variant.id))
    expect(await card.findByRole('link', { name: /Скачать PPTX/ })).toHaveAttribute('href', artifactUrl(decks.faithful.variant.deck_artifact_id ?? ''))
    expect(await card.findByRole('link', { name: /Открыть PDF/ })).toHaveAttribute('target', '_blank')
  })

  it('offers to create a PDF when a variant has none and reports an unavailable export honestly', async () => {
    decks.visual.export.artifacts = decks.visual.export.artifacts.filter((artifact) => artifact.format !== 'pdf')
    renderPage()
    const [, , visual] = await cards()
    const card = within(visual as HTMLElement)
    const button = await card.findByRole('button', { name: 'Создать PDF для превью' })
    expect(card.queryByTestId('pdf-page')).not.toBeInTheDocument()

    await userEvent.click(button)
    await waitFor(() => expect(postSpy).toHaveBeenCalledTimes(1))
    const [path, init] = postSpy.mock.calls[0] as [string, FakeInit]
    expect(path).toBe('/api/v1/variants/{variant_id}/exports')
    expect(init.body).toEqual({ formats: ['pdf'] })
    expect(await card.findByText(/PDF для ревизии не поддерживается/)).toBeInTheDocument()
  })

  it('aligns slides by plan section so an agenda slide does not shift the other variants', async () => {
    decks.visual.slides = decks.visual.slides.slice(0, 10)
    renderPage('?view=compare')

    const table = await screen.findByRole('table', { name: 'Слайд N во всех вариантах' })
    await waitFor(() => expect(within(table).getAllByRole('row')).toHaveLength(1 + 13))
    await waitFor(() => expect(within(table).getAllByText('нет слайда')).toHaveLength(5))

    const rows = within(table).getAllByRole('row')
    const agendaRow = within(rows[2] as HTMLElement)
    expect(agendaRow.getByRole('rowheader')).toHaveTextContent('02')
    expect(agendaRow.getByText('нет слайда')).toBeInTheDocument()
    const agendaLinks = agendaRow.getAllByRole('link')
    expect(agendaLinks).toHaveLength(2)
    expect(agendaLinks[0]).toHaveAttribute('href', `${routes.audit(PROJECT_ID, RUN_ID, decks.balanced.variant.id)}?slide=2`)

    const problemRow = within(rows[4] as HTMLElement)
    const problemLinks = problemRow.getAllByRole('link')
    expect(problemLinks.map((link) => link.getAttribute('href'))).toEqual([
      `${routes.audit(PROJECT_ID, RUN_ID, decks.faithful.variant.id)}?slide=3`,
      `${routes.audit(PROJECT_ID, RUN_ID, decks.balanced.variant.id)}?slide=4`,
      `${routes.audit(PROJECT_ID, RUN_ID, decks.visual.variant.id)}?slide=4`,
    ])
    expect(problemRow.getByText('слайд 3')).toBeInTheDocument()
  })

  it('switches views and keeps the choice in the URL', async () => {
    const router = renderPage()
    await cards()
    await userEvent.click(screen.getByRole('radio', { name: 'По слайдам' }))
    expect(router.state.location.search).toBe('?view=compare')
    expect(await screen.findByRole('table')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('radio', { name: 'Карточки' }))
    expect(router.state.location.search).toBe('')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows the API error envelope message when variants cannot be loaded', async () => {
    variantsFailure = 'Прогон не найден'
    renderPage()
    expect(await screen.findByText('Не удалось загрузить варианты')).toBeInTheDocument()
    expect(screen.getByText(/Прогон не найден/)).toBeInTheDocument()
    expect(screen.getByText(/req_test/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Повторить' })).toBeEnabled()
  })
})
