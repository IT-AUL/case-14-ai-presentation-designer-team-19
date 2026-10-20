import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import {
  normalizeStylePreview,
  useAssistantSession,
  useJobEvents,
  useStartAutopilot,
  useStartStylePreviews,
  useStylePreview,
  type AssistantSession,
  type JobEvent,
  type StylePreview,
  type StylePreviewAccepted,
} from '@/entities/assistant'
import { startGeneration, whenGenerationAccepted, type DeckPlan, type GenerationCreate } from '@/entities/generation'
import { useProject } from '@/entities/project'
import { readPlanRequest, type StoredPlanRequest } from '@/features/edit-deck-plan'
import planFixture from '@/shared/api/mocks/fixtures/assistant/data-plan.json'
import stylePreviewFixture from '@/shared/api/mocks/fixtures/assistant/data-style-preview.json'
import { routes } from '@/shared/config'
import { StylePage } from './StylePage'
import type * as AssistantModule from '@/entities/assistant'
import type * as GenerationModule from '@/entities/generation'
import type * as ProjectModule from '@/entities/project'
import type * as EditPlanModule from '@/features/edit-deck-plan'

vi.mock('@/entities/assistant', async (importOriginal) => ({
  ...(await importOriginal<typeof AssistantModule>()),
  useAssistantSession: vi.fn(),
  useStartAutopilot: vi.fn(),
  useStartStylePreviews: vi.fn(),
  useStylePreview: vi.fn(),
  useJobEvents: vi.fn(),
}))

vi.mock('@/entities/generation', async (importOriginal) => ({
  ...(await importOriginal<typeof GenerationModule>()),
  startGeneration: vi.fn(),
  whenGenerationAccepted: vi.fn(),
}))

vi.mock('@/entities/project', async (importOriginal) => ({
  ...(await importOriginal<typeof ProjectModule>()),
  useProject: vi.fn(),
  rememberRun: vi.fn(),
}))

vi.mock('@/entities/template', () => ({
  useDesignDna: vi.fn(() => ({ data: undefined })),
}))

vi.mock('@/features/edit-deck-plan', async (importOriginal) => ({
  ...(await importOriginal<typeof EditPlanModule>()),
  readPlanRequest: vi.fn(),
  requestAndStorePlan: vi.fn(),
}))

const PROJECT_ID = 'prj_dcfe27bda1874f76b03a28e7ee495aa1'
const PLAN_ID = planFixture.body.plan_id
const PREVIEW_ID = stylePreviewFixture.body.preview_id
const JOB_ID = 'job_79e6951091864f71b03f8e75f7dbd546'
const deckPlan = (planFixture.body.plans[0]?.deck_plan ?? null) as unknown as DeckPlan
const normalizedPreview = normalizeStylePreview(stylePreviewFixture.body)

const storedPlan: StoredPlanRequest = {
  request: {
    template_id: 'tpl_7c4f33420b0e4c1cb783c86d1cfb12c1',
    content_pack_id: 'pack_52549aa7639543a8b68b55c5f60d42a8',
    strategies: ['balanced'],
    brief: {
      purpose: 'Итоги продаж за год для совета директоров',
      audience: 'Совет директоров',
      language: 'ru',
      target_slide_count: 8,
    },
    use_llm: false,
  },
  result: {
    planId: PLAN_ID,
    proposals: [{ strategy: 'balanced', deckPlan }],
    sufficiency: null,
    research: null,
  },
  createdAt: '2026-10-17T13:52:04.311Z',
}

const defaultSession: AssistantSession = {
  step: 5,
  completed_steps: [1, 2, 3, 4],
  skipped_steps: [],
  artifacts: {
    template_id: 'tpl_7c4f33420b0e4c1cb783c86d1cfb12c1',
    content_pack_id: 'pack_52549aa7639543a8b68b55c5f60d42a8',
    brandbook_id: null,
    plan_id: PLAN_ID,
    preview_id: null,
    generation_id: null,
  },
  brief: {
    purpose: 'Итоги продаж за год для совета директоров',
    audience: 'Совет директоров',
    language: 'ru',
    target_slide_count: 8,
  },
  assistant_note: 'Выберите подходящий стиль вёрстки.',
  job_id: null,
}

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>
}

function renderStylePage(initialPath = routes.style(PROJECT_ID)) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/projects/:projectId/style" element={<StylePage />} />
          <Route path="*" element={<div>other screen</div>} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('StylePage', () => {
  const startPreviewsMutate = vi.fn()
  const autopilotMutateAsync = vi.fn()

  beforeEach(() => {
    startPreviewsMutate.mockReset()
    autopilotMutateAsync.mockReset()
    vi.mocked(readPlanRequest).mockReturnValue(storedPlan)
    vi.mocked(useProject).mockReturnValue({ data: undefined } as unknown as ReturnType<typeof useProject>)
    vi.mocked(useAssistantSession).mockReturnValue({
      data: defaultSession,
      isPending: false,
    } as unknown as ReturnType<typeof useAssistantSession>)
    vi.mocked(useStartStylePreviews).mockReturnValue({
      mutate: startPreviewsMutate,
      data: { preview_id: PREVIEW_ID, job_id: JOB_ID, events_url: `/api/v1/jobs/${JOB_ID}/events` } satisfies StylePreviewAccepted,
      isPending: false,
      isSuccess: true,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useStartStylePreviews>)
    vi.mocked(useStylePreview).mockReturnValue({
      data: normalizedPreview,
      isPending: false,
    } as unknown as ReturnType<typeof useStylePreview>)
    vi.mocked(useJobEvents).mockReturnValue({
      events: [],
      lastEvent: null,
      done: true,
      error: null,
    })
    vi.mocked(useStartAutopilot).mockReturnValue({
      mutateAsync: autopilotMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useStartAutopilot>)
    vi.mocked(startGeneration).mockReturnValue('local-run-1')
    vi.mocked(whenGenerationAccepted).mockReturnValue(undefined)
  })

  it('shows three preview columns with unified strategy names and selects the recommended style by default', () => {
    renderStylePage()

    expect(startPreviewsMutate).toHaveBeenCalledWith({})
    expect(screen.getByRole('heading', { level: 1, name: 'Как оформить?' })).toBeInTheDocument()
    expect(screen.getByText('Советую визуальный: на 4 из 6 слайдов графики и схемы.')).toBeInTheDocument()

    const group = screen.getByRole('radiogroup', { name: 'Стиль вёрстки' })
    const faithful = within(group).getByRole('radio', { name: /Близко к шаблону/ })
    const balanced = within(group).getByRole('radio', { name: /Сбалансированный/ })
    const visual = within(group).getByRole('radio', { name: /Визуальный/ })

    expect(faithful).not.toBeChecked()
    expect(balanced).not.toBeChecked()
    expect(visual).toBeChecked()
    expect(visual).toHaveAccessibleName(/советую/)
    expect(faithful).toHaveAccessibleName(/Ближе всего к вашему шаблону/)
    expect(visual).toHaveAccessibleName(/Больше графиков и схем, меньше текста/)
    const table = screen.getByRole('region', { name: 'Пробные слайды' })
    const columns = within(table).getByRole('radiogroup', { name: 'Стиль вёрстки' })
    expect(columns.querySelectorAll('img')).toHaveLength(9)
    for (const strategy of ['faithful', 'balanced', 'visual']) {
      const column = columns.querySelector(`[data-strategy="${strategy}"]`)
      expect(column).not.toBeNull()
      expect(within(column as HTMLElement).getAllByRole('button', { name: /Сравнить слайд/ })).toHaveLength(3)
    }
    expect(columns.querySelector('[data-recommended]')).toHaveAttribute('data-strategy', 'visual')
    expect(screen.getByRole('button', { name: 'Собрать все три' })).toBeEnabled()
  })

  it('renders the build mode switcher in the left column without repeating the styles', async () => {
    renderStylePage()

    const modeSwitch = screen.getByRole('radiogroup', { name: 'Режим сборки' })
    const fastRadio = within(modeSwitch).getByRole('radio', { name: 'Быстро' })
    const checkedRadio = within(modeSwitch).getByRole('radio', { name: 'С проверкой' })

    expect(checkedRadio).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText('С проверкой — проверит числа, источники и плотность каждого слайда')).toBeInTheDocument()

    await userEvent.click(fastRadio)
    expect(fastRadio).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText('Быстро — соберёт колоду за минуту без промежуточной проверки')).toBeInTheDocument()

    expect(screen.queryByLabelText('Памятка по направлениям')).not.toBeInTheDocument()
    expect(screen.getAllByText('Визуальный')).toHaveLength(1)
  })

  it('shows a ready slide alongside slide-shaped skeletons while the other previews arrive', () => {
    const events: JobEvent[] = [
      {
        sequence: 1,
        type: 'preview.ready',
        timestamp: null,
        stage: 'style_preview',
        progress: 0,
        message: null,
        data: {
          strategy: 'visual',
          slide_id: 'slide-0',
          image_artifact_id: 'art_8c02eb48d9884f0c9bc8a50f78c5b8b4',
        },
      },
    ]
    vi.mocked(useJobEvents).mockReturnValue({
      events,
      lastEvent: events[0] ?? null,
      done: false,
      error: null,
    })
    vi.mocked(useStylePreview).mockReturnValue({
      data: null as unknown as StylePreview,
      isPending: true,
    } as unknown as ReturnType<typeof useStylePreview>)

    renderStylePage()

    const table = screen.getByRole('region', { name: 'Пробные слайды' })
    expect(within(table).getAllByRole('button', { name: /Сравнить слайд/ })).toHaveLength(9)
    expect(within(table).getAllByRole('status', { name: /Верстаю/ })).toHaveLength(8)
    expect(within(table).getByRole('button', { name: /Сравнить слайд 1 в трёх стилях \(Визуальный\)/ }).querySelector('img')).not.toBeNull()
  })

  it('opens the comparison when clicking a slide and allows switching slides and styles', async () => {
    renderStylePage()

    await userEvent.click(screen.getByRole('button', { name: /Сравнить слайд 1 в трёх стилях \(Визуальный\)/ }))

    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('heading', { name: /Сравнение трёх стилей · Слайд 01/ })).toBeInTheDocument()
    expect(within(dialog).getByRole('region', { name: 'Стиль «Близко к шаблону»' })).toBeInTheDocument()
    expect(within(dialog).getByRole('region', { name: 'Стиль «Визуальный»' })).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Следующий слайд' }))
    expect(within(dialog).getByRole('heading', { name: /Сравнение трёх стилей · Слайд 02/ })).toBeInTheDocument()

    await userEvent.click(within(dialog).getByRole('button', { name: 'Выбрать «Близко к шаблону»' }))
    expect(screen.getByRole('radio', { name: /Близко к шаблону/, hidden: true })).toBeChecked()
  })

  it('selects a column without opening the comparison and supports keyboard radio navigation', async () => {
    renderStylePage()

    const group = screen.getByRole('radiogroup', { name: 'Стиль вёрстки' })
    await userEvent.click(within(group).getByText('Сбалансированный'))
    expect(within(group).getByRole('radio', { name: /Сбалансированный/ })).toBeChecked()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    within(group).getByRole('radio', { name: /Сбалансированный/ }).focus()
    await userEvent.keyboard('{ArrowLeft}')
    expect(within(group).getByRole('radio', { name: /Близко к шаблону/ })).toBeChecked()
  })

  it('starts generation in the chosen style with deck_plan and preview_id, or in all three styles', async () => {
    const { unmount } = renderStylePage()

    await userEvent.click(screen.getByRole('button', { name: 'Собрать только выбранный' }))
    expect(startGeneration).toHaveBeenCalledWith(
      PROJECT_ID,
      expect.objectContaining({
        template_id: storedPlan.request.template_id,
        content_pack_id: storedPlan.request.content_pack_id,
        preview_id: PREVIEW_ID,
        variants: [{ strategy: 'visual' }],
        deck_plan: expect.objectContaining({ id: deckPlan.id }),
      } satisfies Partial<GenerationCreate>),
    )
    expect(screen.getByTestId('location')).toHaveTextContent(routes.run(PROJECT_ID, 'local-run-1'))

    unmount()
    vi.mocked(startGeneration).mockClear()
    renderStylePage()

    await userEvent.click(screen.getByRole('button', { name: 'Собрать все три' }))
    expect(startGeneration).toHaveBeenCalledWith(
      PROJECT_ID,
      expect.objectContaining({
        preview_id: PREVIEW_ID,
        variants: [{ strategy: 'faithful' }, { strategy: 'balanced' }, { strategy: 'visual' }],
      }),
    )
  })

  it('reuses the trial slides already made for this plan instead of starting new ones', () => {
    vi.mocked(useAssistantSession).mockReturnValue({
      data: { ...defaultSession, artifacts: { ...defaultSession.artifacts, preview_id: PREVIEW_ID } },
      isPending: false,
    } as unknown as ReturnType<typeof useAssistantSession>)
    vi.mocked(useStartStylePreviews).mockReturnValue({
      mutate: startPreviewsMutate,
      data: undefined,
      isPending: false,
      isSuccess: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useStartStylePreviews>)

    renderStylePage()

    expect(startPreviewsMutate).not.toHaveBeenCalled()
    expect(screen.getByRole('radiogroup', { name: 'Стиль вёрстки' })).toBeInTheDocument()
  })

  it('starts autopilot from step 5 when clicking «Сделай сам»', async () => {
    autopilotMutateAsync.mockResolvedValue({
      job_id: 'job_autopilot_5',
      events_url: '/api/v1/jobs/job_autopilot_5/events',
      assistant_note: 'Собираю колоду.',
    })
    renderStylePage()

    await userEvent.click(screen.getByRole('button', { name: /Сделай сам/ }))
    expect(autopilotMutateAsync).toHaveBeenCalledWith({
      from_step: 5,
      use_llm: false,
    })
    expect(screen.getByTestId('location')).toHaveTextContent(routes.run(PROJECT_ID, 'job_autopilot_5'))
  })

  it('degrades gracefully when /style-previews returns 404/501 (null)', () => {
    vi.mocked(useStartStylePreviews).mockReturnValue({
      mutate: startPreviewsMutate,
      data: null,
      isPending: false,
      isSuccess: true,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useStartStylePreviews>)
    vi.mocked(useStylePreview).mockReturnValue({
      data: null,
      isPending: false,
    } as unknown as ReturnType<typeof useStylePreview>)

    renderStylePage()

    expect(screen.getByRole('radiogroup', { name: 'Стиль вёрстки' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Собрать только выбранный' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Собрать все три' })).toBeEnabled()
  })

  it('sends back to the plan when no plan or materials exist yet', async () => {
    vi.mocked(readPlanRequest).mockReturnValue(null)
    vi.mocked(useAssistantSession).mockReturnValue({
      data: {
        ...defaultSession,
        artifacts: {
          template_id: null,
          content_pack_id: null,
          brandbook_id: null,
          plan_id: null,
          preview_id: null,
          generation_id: null,
        },
      },
      isPending: false,
    } as unknown as ReturnType<typeof useAssistantSession>)

    renderStylePage()

    expect(screen.getByText('Сначала согласуйте план — потом примерю стили.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'К плану' }))
    expect(screen.getByTestId('location')).toHaveTextContent(routes.planDraft(PROJECT_ID))
  })
})
