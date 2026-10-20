import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { useJobEvents, type JobEvent } from '@/entities/assistant'
import { useGenerationTracker, useRetryGeneration, useSlideDrafts, type GenerationTracker } from '@/entities/generation'
import { markEvidence, projectRunId, rememberRun } from '@/entities/project'
import { api, ApiError } from '@/shared/api'
import { generationFixture } from '@/shared/api/mocks'
import { routes } from '@/shared/config'
import { ToastProvider } from '@/shared/ui'
import { GenerationPage } from './GenerationPage'
import type * as AssistantModule from '@/entities/assistant'
import type * as GenerationModule from '@/entities/generation'
import type * as ProjectModule from '@/entities/project'

vi.mock('@/entities/assistant', async (importOriginal) => ({
  ...(await importOriginal<typeof AssistantModule>()),
  useJobEvents: vi.fn(() => ({ events: [], lastEvent: null, done: false, error: null })),
}))

vi.mock('@/entities/generation', async (importOriginal) => ({
  ...(await importOriginal<typeof GenerationModule>()),
  useGenerationTracker: vi.fn(),
  useRetryGeneration: vi.fn(),
  useSlideDrafts: vi.fn(() => ({ data: null })),
}))

vi.mock('@/entities/project', async (importOriginal) => ({
  ...(await importOriginal<typeof ProjectModule>()),
  markEvidence: vi.fn(),
  projectRunId: vi.fn(),
  rememberRun: vi.fn(),
  useProject: vi.fn(() => ({ data: undefined })),
}))

vi.mock('@/features/variant-files', () => ({
  useVariantFiles: () => ({ variant: undefined, files: null, pdfUrl: null, pptxUrl: null, passportArtifactId: null, isPending: false, error: null }),
}))

const PROJECT = 'prj_1'
const RUN = generationFixture.id
const TRACKING = 'local-abc-1'
const retryMutate = vi.fn()

function makeTracker(overrides: Partial<GenerationTracker>): GenerationTracker {
  return {
    trackingId: null,
    generationId: null,
    jobId: null,
    variantIds: [],
    phase: 'running',
    startedAt: Date.now() - 5_000,
    finishedAt: null,
    generation: undefined,
    job: undefined,
    error: null,
    jobError: null,
    canCancel: false,
    ...overrides,
  }
}

function useTracker(tracker: GenerationTracker) {
  vi.mocked(useGenerationTracker).mockReturnValue(tracker)
}

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>
}

function renderPage(runId: string, state?: Record<string, unknown>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const entry = state ? { pathname: routes.run(PROJECT, runId), state } : routes.run(PROJECT, runId)
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={[entry]}>
          <Routes>
            <Route path="/projects/:projectId/runs/:runId" element={<GenerationPage />} />
            <Route path="*" element={<div>other screen</div>} />
          </Routes>
          <LocationProbe />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

const completed = { ...generationFixture, state: 'completed' as const }
const running = {
  ...generationFixture,
  state: 'running' as const,
  finished_at: null,
  variants: generationFixture.variants.map((variant, index) => ({
    ...variant,
    status: (['completed', 'running', 'queued'] as const)[index] ?? 'queued',
  })),
}

describe('GenerationPage', () => {
  beforeEach(() => {
    retryMutate.mockReset()
    vi.mocked(useRetryGeneration).mockReturnValue({ mutate: retryMutate, isPending: false } as unknown as ReturnType<typeof useRetryGeneration>)
    vi.mocked(useJobEvents).mockReturnValue({ events: [], lastEvent: null, done: false, error: null })
    vi.mocked(useSlideDrafts).mockReturnValue({ data: null } as unknown as ReturnType<typeof useSlideDrafts>)
    vi.mocked(projectRunId).mockReturnValue(undefined)
    vi.mocked(markEvidence).mockClear()
    vi.mocked(rememberRun).mockClear()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('shows the styles being prepared without a countdown while the request is pending', () => {
    useTracker(makeTracker({ trackingId: TRACKING, phase: 'submitting' }))
    renderPage(TRACKING)

    expect(screen.getByRole('heading', { level: 1, name: 'Собираю презентацию' })).toBeInTheDocument()
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
    expect(screen.queryByText(/5:00/)).not.toBeInTheDocument()
    const styles = within(screen.getByRole('list', { name: 'Стили сборки' }))
    for (const name of ['Близко к шаблону', 'Сбалансированный', 'Визуальный']) {
      expect(styles.getByRole('button', { name: new RegExp(`^${name}`) })).toBeInTheDocument()
    }
    expect(screen.getByRole('button', { name: 'Отменить' })).toBeDisabled()
    expect(screen.queryByText(/%/)).not.toBeInTheDocument()
  })

  it('swaps the tracking id for the real run id once the service accepts', () => {
    useTracker(makeTracker({ trackingId: TRACKING, generationId: RUN, phase: 'running' }))
    renderPage(TRACKING)

    expect(rememberRun).toHaveBeenCalledWith(PROJECT, RUN)
    expect(screen.getByTestId('location')).toHaveTextContent(routes.run(PROJECT, RUN))
  })

  it('shows per-style statuses and allows cancelling an asynchronous run', () => {
    useTracker(makeTracker({ generationId: RUN, generation: running, phase: 'running', canCancel: true, job: { ...running, id: 'job', kind: 'generation', stage: 'audit', progress: 0.5, result_ids: {} } }))
    renderPage(RUN)

    const styles = within(screen.getByRole('list', { name: 'Стили сборки' }))
    expect(styles.getByRole('button', { name: /^Близко к шаблону.*готово/ })).toHaveAttribute('aria-pressed', 'false')
    expect(styles.getByRole('button', { name: /^Сбалансированный.*собираю/ })).toHaveAttribute('aria-pressed', 'true')
    expect(styles.getByRole('button', { name: /^Визуальный.*в очереди/ })).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Слайды презентации' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Отменить' })).toBeEnabled()
    expect(rememberRun).not.toHaveBeenCalled()
  })

  it('always shows build stages and progress in the left column even when building a single style', () => {
    const singleVariantRunning = {
      ...running,
      variants: running.variants.slice(1, 2).map((variant) => ({
        ...variant,
        id: 'var_single_balanced',
        strategy: 'balanced' as const,
        status: 'running' as const,
      })),
    }
    useTracker(makeTracker({ generationId: RUN, generation: singleVariantRunning, phase: 'running', canCancel: true }))
    renderPage(RUN)

    const stages = within(screen.getByRole('list', { name: 'Этапы сборки' }))
    expect(stages.getByText('План')).toBeInTheDocument()
    expect(stages.getByText(/Слайды 0 из 12/)).toBeInTheDocument()
    expect(stages.getByText('Проверка')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Прогресс сборки' })).toBeInTheDocument()
    expect(screen.getByLabelText('Стиль сборки')).toHaveTextContent('Сбалансированный')
    expect(screen.queryByRole('list', { name: 'Стили сборки' })).not.toBeInTheDocument()
    expect(screen.getByText('из 12 слайдов готово')).toBeInTheDocument()
    expect(stages.getByText('Верстаю слайды в вашем шаблоне по одному')).toBeInTheDocument()
    expect(screen.queryByText('Ожидает очереди рендера')).not.toBeInTheDocument()
    expect(screen.getAllByText('Верстаю этот слайд…')).toHaveLength(1)
  })

  it('replaces cards with live PNG previews, shows draft/superseded badges, and opens slide inspector on click', async () => {
    const singleVariantRunning = {
      ...running,
      variants: running.variants.slice(1, 2).map((variant) => ({
        ...variant,
        id: 'var_live',
        strategy: 'balanced' as const,
        status: 'running' as const,
      })),
    }
    const events: JobEvent[] = [
      {
        sequence: 1,
        type: 'slide.ready',
        timestamp: '2026-10-18T12:00:00Z',
        jobId: 'job_1',
        stage: 'render',
        progress: 0.2,
        message: null,
        data: {
          variant_id: 'var_live',
          slide_key: 'var_live:1',
          index: 1,
          slide_id: 's1',
          preview_revision: 1,
          status: 'draft',
          preview_artifact_id: 'art_slide_1_draft',
        },
      },
      {
        sequence: 2,
        type: 'slide.ready',
        timestamp: '2026-10-18T12:00:02Z',
        jobId: 'job_1',
        stage: 'render',
        progress: 0.3,
        message: null,
        data: {
          variant_id: 'var_live',
          slide_key: 'var_live:2',
          index: 2,
          slide_id: 's2',
          preview_revision: 1,
          status: 'superseded',
          preview_artifact_id: 'art_slide_2_draft',
        },
      },
      {
        sequence: 3,
        type: 'slide.ready',
        timestamp: '2026-10-18T12:00:04Z',
        jobId: 'job_1',
        stage: 'render',
        progress: 0.4,
        message: null,
        data: {
          variant_id: 'var_live',
          slide_key: 'var_live:3',
          index: 3,
          slide_id: 's3',
          preview_revision: 2,
          status: 'rendered',
          preview_artifact_id: 'art_slide_3_final',
        },
      },
    ]
    vi.mocked(useJobEvents).mockReturnValue({ events, lastEvent: events[2] ?? null, done: false, error: null })
    useTracker(makeTracker({ generationId: RUN, generation: singleVariantRunning, phase: 'running', canCancel: true }))
    renderPage(RUN)

    const grid = within(screen.getByRole('list', { name: 'Слайды презентации' }))
    expect(grid.getAllByText('черновик').length).toBeGreaterThanOrEqual(1)
    expect(grid.getByText('обновляется')).toBeInTheDocument()

    const stages = within(screen.getByRole('list', { name: 'Этапы сборки' }))
    expect(stages.getByText('Слайды 3 из 12')).toBeInTheDocument()

    await userEvent.click(grid.getByRole('button', { name: /Слайд 1:/ }))
    expect(screen.getByRole('complementary', { name: /^01 ·/ })).toBeInTheDocument()
  })

  it('restores slide previews from GET /variants/{id}/slide-drafts after refresh when SSE is empty', () => {
    const singleVariantRunning = {
      ...running,
      variants: running.variants.slice(1, 2).map((variant) => ({
        ...variant,
        id: 'var_refresh',
        strategy: 'balanced' as const,
        status: 'running' as const,
      })),
    }
    vi.mocked(useSlideDrafts).mockReturnValue({
      data: {
        variant_id: 'var_refresh',
        state: 'running',
        slides: [
          {
            variant_id: 'var_refresh',
            index: 1,
            slide_id: 's1',
            slide_key: 'var_refresh:1',
            revision: 1,
            preview_revision: 2,
            preview_artifact_id: 'art_restored_1',
            status: 'rendered',
          },
          {
            variant_id: 'var_refresh',
            index: 2,
            slide_id: 's2',
            slide_key: 'var_refresh:2',
            revision: 1,
            preview_revision: 1,
            preview_artifact_id: 'art_restored_2',
            status: 'draft',
          },
        ],
      },
    } as unknown as ReturnType<typeof useSlideDrafts>)
    useTracker(makeTracker({ generationId: RUN, generation: singleVariantRunning, phase: 'running', canCancel: true }))
    renderPage(RUN)

    const stages = within(screen.getByRole('list', { name: 'Этапы сборки' }))
    expect(stages.getByText('Слайды 2 из 12')).toBeInTheDocument()
    const grid = within(screen.getByRole('list', { name: 'Слайды презентации' }))
    expect(grid.getByText('черновик')).toBeInTheDocument()
  })

  it('offers to open the finished deck when done and allows staying when auto-polish countdown is shown', async () => {
    vi.useFakeTimers()
    const singleCompleted = {
      ...completed,
      variants: completed.variants.slice(1, 2),
    }
    const startedAt = Date.parse(completed.created_at)
    useTracker(makeTracker({ generationId: RUN, generation: singleCompleted, phase: 'completed', startedAt, finishedAt: startedAt + 8_196 }))
    renderPage(RUN)

    expect(screen.getByRole('heading', { level: 1, name: 'Презентация готова' })).toBeInTheDocument()
    expect(screen.getByText('Проверьте слайды и доработайте то, что нужно.')).toBeInTheDocument()
    expect(screen.getByText('Этапы завершены').closest('details')).not.toHaveAttribute('open')
    expect(screen.queryByRole('progressbar', { name: 'Прогресс сборки' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Отменить' })).not.toBeInTheDocument()
    expect(markEvidence).toHaveBeenCalledWith(PROJECT, 'generated')
    expect(screen.getByText(/Открою доводку через 3 с/)).toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(900)
    })
    expect(screen.getByText(/Открою доводку через 2 с/)).toBeInTheDocument()
    expect(screen.getByTestId('location')).toHaveTextContent(routes.run(PROJECT, RUN))

    vi.useRealTimers()
    await userEvent.click(screen.getByRole('button', { name: 'Остаться здесь' }))
    expect(screen.queryByText(/Открою доводку через/)).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Открыть и доработать' }))
    expect(screen.getByTestId('location')).toHaveTextContent(routes.polish(PROJECT, RUN))
  })

  it('does not start auto-polish countdown when navigated explicitly from StepBar', () => {
    const singleCompleted = {
      ...completed,
      variants: completed.variants.slice(1, 2),
    }
    const startedAt = Date.parse(completed.created_at)
    useTracker(makeTracker({ generationId: RUN, generation: singleCompleted, phase: 'completed', startedAt, finishedAt: startedAt + 8_196 }))
    renderPage(RUN, { fromStepBar: true })

    expect(screen.getByRole('heading', { level: 1, name: 'Презентация готова' })).toBeInTheDocument()
    expect(screen.queryByText(/Открою доводку через/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Открыть и доработать' })).toBeInTheDocument()
  })

  it('explains a failure from the error envelope and retries the same run', async () => {
    retryMutate.mockImplementation((_id: string, options: { onSuccess: (trackingId: string) => void }) => options.onSuccess('local-retry-2'))
    const failed = { ...generationFixture, state: 'failed' as const, variants: generationFixture.variants.map((variant) => ({ ...variant, status: 'failed' as const })) }
    const failedTracker = makeTracker({
      generationId: RUN,
      generation: failed,
      phase: 'failed',
      finishedAt: Date.now(),
      jobError: { code: 'llm_timeout', message: 'Модель не вернула план в срок', stage: 'deck_plan' },
    })
    vi.mocked(useGenerationTracker).mockImplementation((id) => (id === RUN ? failedTracker : makeTracker({ trackingId: id ?? null, phase: 'submitting' })))
    renderPage(RUN)

    const alert = screen.getByRole('alert')
    expect(within(alert).getByText('Модель не вернула план в срок')).toBeInTheDocument()
    expect(within(alert).getByText('LLM_TIMEOUT · этап: план структуры')).toBeInTheDocument()
    expect(within(alert).getByRole('link', { name: 'К плану' })).toHaveAttribute('href', routes.planDraft(PROJECT))
    expect(screen.getByRole('heading', { level: 1, name: 'Не получилось собрать' })).toBeInTheDocument()
    expect(markEvidence).not.toHaveBeenCalled()

    await userEvent.click(within(alert).getByRole('button', { name: 'Повторить' }))
    expect(retryMutate).toHaveBeenCalledWith(RUN, expect.anything())
    expect(screen.getByTestId('location')).toHaveTextContent(routes.run(PROJECT, 'local-retry-2'))
    expect(screen.getByRole('heading', { level: 1, name: 'Собираю презентацию' })).toBeInTheDocument()
  })

  it('keeps the scene and shows a calm banner while the connection is lost', async () => {
    const refetch = vi.fn()
    const error = new ApiError({ code: 'internal_error', message: 'HTTP 502', status: 502 })
    useTracker(makeTracker({ generationId: RUN, generation: running, phase: 'running', canCancel: true, error, reconnecting: true, refetch }))
    renderPage(RUN)

    expect(screen.getByRole('heading', { level: 2, name: 'Нет связи с сервисом — повторяем…' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Собираю презентацию' })).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Стили сборки' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Проверить сейчас' }))
    expect(refetch).toHaveBeenCalledTimes(1)
    expect(retryMutate).not.toHaveBeenCalled()
  })

  it('refetches the status instead of starting a new run when only the status request failed', async () => {
    const refetch = vi.fn()
    const error = new ApiError({ code: 'not_found', message: 'Прогон не найден', status: 404 })
    useTracker(makeTracker({ generationId: RUN, phase: 'failed', error, refetch }))
    renderPage(RUN)

    await userEvent.click(within(screen.getByRole('alert')).getByRole('button', { name: 'Повторить' }))
    expect(refetch).toHaveBeenCalledTimes(1)
    expect(retryMutate).not.toHaveBeenCalled()
  })

  it('offers only the way back when a rejected request produced no run', () => {
    const error = new ApiError({ code: 'validation_error', message: 'Бриф не прошёл проверку', status: 422 })
    useTracker(makeTracker({ trackingId: TRACKING, phase: 'failed', error, finishedAt: Date.now() }))
    renderPage(TRACKING)

    const alert = screen.getByRole('alert')
    expect(within(alert).getByText('Бриф не прошёл проверку')).toBeInTheDocument()
    expect(within(alert).queryByRole('button', { name: 'Повторить' })).not.toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Стили сборки' })).not.toBeInTheDocument()
  })

  it('gives a recovery path when the page was reloaded during the synchronous request', () => {
    vi.mocked(projectRunId).mockReturnValue('run_previous')
    const error = new ApiError({ code: 'tracking_lost', message: 'Страница была перезагружена до ответа сервиса, результат этого запуска недоступен', status: 0 })
    useTracker(makeTracker({ phase: 'failed', error, startedAt: null }))
    renderPage(TRACKING)

    expect(screen.getByRole('heading', { level: 1, name: 'Сборка потерялась' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Связь с запуском потеряна' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Открыть последний прогон' })).toHaveAttribute('href', routes.run(PROJECT, 'run_previous'))
    expect(screen.getByRole('link', { name: 'К плану' })).toHaveAttribute('href', routes.planDraft(PROJECT))
    expect(screen.queryByRole('timer')).not.toBeInTheDocument()
  })

  it('queues a slide edit from a ready slide card during build and allows cancelling it', async () => {
    const user = userEvent.setup()
    const singleVariantRunning = {
      ...running,
      variants: running.variants.slice(1, 2).map((variant) => ({
        ...variant,
        id: 'var_build_edit',
        strategy: 'balanced' as const,
        status: 'running' as const,
      })),
    }
    const events: JobEvent[] = [
      {
        sequence: 1,
        type: 'slide.ready',
        timestamp: '2026-10-18T12:00:00Z',
        jobId: 'job_1',
        stage: 'render',
        progress: 0.2,
        message: null,
        data: {
          variant_id: 'var_build_edit',
          slide_key: 'var_build_edit:1',
          index: 1,
          slide_id: 'slide-0',
          preview_revision: 2,
          status: 'rendered',
          preview_artifact_id: 'art_slide_1_ready',
        },
      },
    ]

    let queuedItems: Record<string, unknown>[] = []
    vi.spyOn(api, 'GET').mockImplementation(((path: string) => {
      if (path === '/api/v1/variants/{variant_id}/changes') {
        return Promise.resolve({
          data: {
            variant_id: 'var_build_edit',
            applied_revision: 1,
            accepted_revision: queuedItems.some((item) => item.state === 'queued') ? 2 : 1,
            items: queuedItems,
          },
          error: undefined,
          response: new Response(null, { status: 200 }),
        }) as never
      }
      return Promise.resolve({
        data: undefined,
        error: { error: { code: 'not_found', message: path } },
        response: new Response(null, { status: 404 }),
      }) as never
    }) as unknown as typeof api.GET)

    const postSpy = vi.spyOn(api, 'POST').mockImplementation(((path: string, init?: unknown) => {
      const body = (init as { body?: { slide_id?: string; expected_revision?: number; action?: string } } | undefined)?.body
      if (path === '/api/v1/variants/{variant_id}/changes') {
        const created = {
          change_id: 'chg_build_1',
          variant_id: 'var_build_edit',
          slide_id: body?.slide_id ?? 'slide-0',
          action: body?.action ?? 'shorter',
          state: 'queued',
          base_revision: body?.expected_revision ?? 1,
          target_revision: 2,
        }
        queuedItems = [created]
        return Promise.resolve({
          data: created,
          error: undefined,
          response: new Response(null, { status: 202 }),
        }) as never
      }
      if (path === '/api/v1/variants/{variant_id}/changes/{change_id}/cancel') {
        const cancelled = {
          ...(queuedItems[0] ?? {}),
          change_id: 'chg_build_1',
          state: 'canceled',
        }
        queuedItems = [cancelled]
        return Promise.resolve({
          data: cancelled,
          error: undefined,
          response: new Response(null, { status: 200 }),
        }) as never
      }
      return Promise.resolve({
        data: undefined,
        error: { error: { code: 'not_found', message: path } },
        response: new Response(null, { status: 404 }),
      }) as never
    }) as unknown as typeof api.POST)

    vi.mocked(useJobEvents).mockReturnValue({ events, lastEvent: events[0] ?? null, done: false, error: null })
    useTracker(makeTracker({ generationId: RUN, generation: singleVariantRunning, phase: 'running', canCancel: true }))
    renderPage(RUN)

    const grid = within(screen.getByRole('list', { name: 'Слайды презентации' }))
    const editBtn = grid.getByRole('button', { name: 'Короче' })
    await user.click(editBtn)

    expect(postSpy).toHaveBeenCalledWith(
      '/api/v1/variants/{variant_id}/changes',
      expect.objectContaining({
        params: { path: { variant_id: 'var_build_edit' } },
      }),
    )
    expect(await grid.findByText('правка в очереди')).toBeInTheDocument()
    const cancelCardBtn = grid.getByRole('button', { name: 'Отменить' })
    await user.click(cancelCardBtn)

    expect(await grid.findByRole('button', { name: 'Короче' })).toBeInTheDocument()
    expect(grid.queryByText('правка в очереди')).not.toBeInTheDocument()
    vi.restoreAllMocks()
  })

  it('caps the slide progress counter at 9 of 9 across 27 multi-variant slide events and clears it on failure', () => {
    const basePlan = running.deck_plan
    const nineSlideDeckPlan = basePlan
      ? {
          ...basePlan,
          slides: basePlan.slides.slice(0, 9),
        }
      : null
    const threeVariantRunning = {
      ...running,
      deck_plan: nineSlideDeckPlan,
      variants: running.variants.map((variant, index) => ({
        ...variant,
        id: (['var_f', 'var_b', 'var_v'] as const)[index] ?? 'var_v',
        strategy: (['faithful', 'balanced', 'visual'] as const)[index] ?? 'visual',
        status: (['completed', 'running', 'running'] as const)[index] ?? 'running',
      })),
    }
    const events: JobEvent[] = []
    let sequence = 1
    for (const variant of ['var_f', 'var_b', 'var_v']) {
      for (let slideNum = 1; slideNum <= 9; slideNum += 1) {
        events.push({
          sequence,
          type: 'slide.ready',
          timestamp: '2026-10-18T12:00:00Z',
          jobId: 'job_1',
          stage: 'render',
          progress: 0.5,
          message: null,
          data: {
            variant_id: variant,
            slide_key: `${variant}:${slideNum}`,
            index: slideNum,
            slide_id: `${variant}_s_${slideNum}`,
            preview_revision: 1,
            status: 'draft',
            preview_artifact_id: `art_${sequence}`,
          },
        })
        sequence += 1
      }
    }
    vi.mocked(useJobEvents).mockReturnValue({ events, lastEvent: events[events.length - 1] ?? null, done: false, error: null })
    useTracker(makeTracker({ generationId: RUN, generation: threeVariantRunning, phase: 'running', canCancel: true }))
    const { unmount } = renderPage(RUN)

    expect(screen.getByText('Собираю 9 из 9…')).toBeInTheDocument()
    expect(screen.queryByText(/27 из 9/)).not.toBeInTheDocument()
    const stages = within(screen.getByRole('list', { name: 'Этапы сборки' }))
    expect(stages.getByText('Слайды 9 из 9')).toBeInTheDocument()
    unmount()

    const threeVariantFailed = {
      ...threeVariantRunning,
      state: 'failed' as const,
      variants: threeVariantRunning.variants.map((v) => ({ ...v, status: 'failed' as const })),
    }
    useTracker(
      makeTracker({
        generationId: RUN,
        generation: threeVariantFailed,
        phase: 'failed',
        finishedAt: Date.now(),
        jobError: { code: 'layout_error', message: 'Ошибка раскладки', stage: 'layout_mapping' },
      }),
    )
    renderPage(RUN)

    expect(screen.getByRole('heading', { level: 1, name: 'Не получилось собрать' })).toBeInTheDocument()
    expect(screen.getByText('Бриф и файлы сохранены.')).toBeInTheDocument()
    expect(screen.queryByText(/Собираю/)).not.toBeInTheDocument()
  })

  it('shows facts_by_origin, clickable web.sources links, and shortage explanation when content_fill is returned', () => {
    const completedWithContentFill = {
      ...completed,
      deck_plan: completed.deck_plan
        ? {
            ...completed.deck_plan,
            slides: completed.deck_plan.slides.slice(0, 3),
          }
        : null,
      content_fill: {
        status: 'short',
        mode: 'shrink',
        before: { requested: 12, capacity: 3, status: 'short', total_facts: 2 },
        after: { requested: 3, capacity: 3, status: 'ok', total_facts: 6 },
        shortage_reason: 'Собрал 3 слайда вместо 12: в исходном запросе не было таблиц и документов.',
        facts_by_origin: { user: 2, web: 4, llm: 0 },
        web: {
          queries: ['DNS аналитика'],
          sources: [
            {
              id: 'src-dns-1',
              title: 'DNS: итоги 2025 года и сеть магазинов',
              url: 'https://www.dns-shop.ru/about/',
              facts: 3,
            },
          ],
        },
      },
    }
    const startedAt = Date.parse(completed.created_at)
    useTracker(
      makeTracker({
        generationId: RUN,
        generation: completedWithContentFill as unknown as typeof completed,
        phase: 'completed',
        startedAt,
        finishedAt: startedAt + 5_000,
      }),
    )
    renderPage(RUN, { fromStepBar: true })

    const summary = screen.getByLabelText('Материалы и источники')
    expect(summary).toHaveTextContent('Собрал 3 слайда вместо 12: в исходном запросе не было таблиц и документов.')
    expect(summary).toHaveTextContent('Факты: из ваших материалов — 2, из открытых источников — 4.')
    const link = within(summary).getByRole('link', { name: 'DNS: итоги 2025 года и сеть магазинов' })
    expect(link).toHaveAttribute('href', 'https://www.dns-shop.ru/about/')
  })
})

