import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import {
  useAddNotes,
  useAssistantSession,
  useQuestions,
  useStartAutopilot,
  useSubmitAnswers,
  useUnderstanding,
  type AssistantSession,
  type QuestionsResult,
  type Understanding,
} from '@/entities/assistant'
import { useInvalidateProject, useProject } from '@/entities/project'
import { useActiveProviderSession } from '@/entities/provider-session'
import { useCapabilityFlag } from '@/entities/system'
import { usePlanBuild } from '@/features/edit-deck-plan'
import questionsFixture from '@/shared/api/mocks/fixtures/assistant/data-questions.json'
import understandingFixture from '@/shared/api/mocks/fixtures/assistant/data-understanding.json'
import { QuestionsPage } from './QuestionsPage'
import type * as AssistantModule from '@/entities/assistant'
import type * as ProjectModule from '@/entities/project'
import type * as ProviderSessionModule from '@/entities/provider-session'
import type * as SystemModule from '@/entities/system'
import type * as EditPlanModule from '@/features/edit-deck-plan'

vi.mock('@/entities/assistant', async (importOriginal) => ({
  ...(await importOriginal<typeof AssistantModule>()),
  useAssistantSession: vi.fn(),
  useQuestions: vi.fn(),
  useSubmitAnswers: vi.fn(),
  useAddNotes: vi.fn(),
  useStartAutopilot: vi.fn(),
  useUnderstanding: vi.fn(),
}))

vi.mock('@/entities/project', async (importOriginal) => ({
  ...(await importOriginal<typeof ProjectModule>()),
  useProject: vi.fn(),
  useInvalidateProject: vi.fn(),
}))

vi.mock('@/entities/provider-session', async (importOriginal) => ({
  ...(await importOriginal<typeof ProviderSessionModule>()),
  useActiveProviderSession: vi.fn(),
}))

vi.mock('@/entities/system', async (importOriginal) => ({
  ...(await importOriginal<typeof SystemModule>()),
  useCapabilityFlag: vi.fn(),
}))

vi.mock('@/features/edit-deck-plan', async (importOriginal) => ({
  ...(await importOriginal<typeof EditPlanModule>()),
  usePlanBuild: vi.fn(),
  requestAndStorePlan: vi.fn(),
}))

const PROJECT_ID = 'prj_dcfe27bda1874f76b03a28e7ee495aa1'

const sessionData: AssistantSession = {
  step: 3,
  completed_steps: [1, 2],
  skipped_steps: [],
  artifacts: {
    template_id: 'tpl_1',
    content_pack_id: 'pack_1',
    brandbook_id: null,
    plan_id: null,
    preview_id: null,
    generation_id: null,
  },
  brief: {},
  assistant_note: 'Пара уточнений, и составлю план.',
  job_id: null,
}

function renderQuestionsPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/projects/${PROJECT_ID}/questions`]}>
        <Routes>
          <Route path="/projects/:projectId/questions" element={<QuestionsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('QuestionsPage', () => {
  beforeEach(() => {
    vi.mocked(useProject).mockReturnValue({
      data: { id: PROJECT_ID, name: 'Тестовый проект', default_language: 'ru', target_slide_count: 8 },
      isLoading: false,
      error: null,
    } as unknown as ReturnType<typeof useProject>)
    vi.mocked(useInvalidateProject).mockReturnValue(vi.fn())
    vi.mocked(useAssistantSession).mockReturnValue({
      data: sessionData,
      isLoading: false,
    } as unknown as ReturnType<typeof useAssistantSession>)
    vi.mocked(useActiveProviderSession).mockReturnValue(null)
    vi.mocked(useCapabilityFlag).mockReturnValue(false)
    vi.mocked(useQuestions).mockReturnValue({
      data: questionsFixture.body as unknown as QuestionsResult,
      isLoading: false,
      isSuccess: true,
    } as unknown as ReturnType<typeof useQuestions>)
    vi.mocked(useUnderstanding).mockReturnValue({
      data: understandingFixture.body as unknown as Understanding,
      isLoading: false,
    } as unknown as ReturnType<typeof useUnderstanding>)
    vi.mocked(useSubmitAnswers).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useSubmitAnswers>)
    vi.mocked(useAddNotes).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useAddNotes>)
    vi.mocked(useStartAutopilot).mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    } as unknown as ReturnType<typeof useStartAutopilot>)
    vi.mocked(usePlanBuild).mockReturnValue({
      begin: vi.fn(() => ({})),
      enterPhase: vi.fn(),
      finish: vi.fn(),
      cancel: vi.fn(),
      progress: null,
      phase: 'idle',
      startedAt: null,
    } as unknown as ReturnType<typeof usePlanBuild>)
  })

  it('renders choice cards with explanations, recommended badge, and live deck specification on the table', () => {
    renderQuestionsPage()

    expect(screen.getByRole('heading', { level: 1, name: 'Для кого эта презентация?' })).toBeInTheDocument()
    const group = screen.getByRole('radiogroup', { name: 'Для кого эта презентация?' })
    const defaultOption = within(group).getByRole('radio', { name: /Пока не определено/ })
    const boardOption = within(group).getByRole('radio', { name: /Совет директоров/ })

    expect(defaultOption).toBeChecked()
    expect(defaultOption).toHaveAccessibleName(/советую/)
    expect(boardOption).toHaveAccessibleName(/Финансовый эффект/)

    const specRegion = screen.getByRole('region', { name: 'Спецификация будущей колоды' })
    const specList = within(specRegion).getByRole('list', { name: 'Параметры будущей колоды' })
    expect(within(specRegion).getByRole('heading', { name: 'Итоги продаж за год для совета директоров' })).toBeInTheDocument()
    expect(within(specList).getByText('8 слайдов')).toBeInTheDocument()
    expect(within(specList).getByText('Выручка: рост в 2,1 раза за 24 месяца')).toBeInTheDocument()
  })

  it('selects options via keyboard keys 1–5 and updates the live specification immediately', async () => {
    renderQuestionsPage()

    const group = screen.getByRole('radiogroup', { name: 'Для кого эта презентация?' })
    const boardOption = within(group).getByRole('radio', { name: /Совет директоров/ })
    expect(boardOption).not.toBeChecked()

    await userEvent.keyboard('3')
    expect(boardOption).toBeChecked()

    const specList = screen.getByRole('list', { name: 'Параметры будущей колоды' })
    expect(within(specList).getByText('Совет директоров')).toBeInTheDocument()
    expect(within(specList).getByText(/Финансовый эффект, стратегия и решения/)).toBeInTheDocument()
  })

  it('opens the custom option input field and reflects custom text in the live specification', async () => {
    renderQuestionsPage()

    const group = screen.getByRole('radiogroup', { name: 'Для кого эта презентация?' })
    const customRadio = within(group).getByRole('radio', { name: /Свой вариант/ })
    await userEvent.click(customRadio)

    expect(customRadio).toBeChecked()
    const input = screen.getByRole('textbox', { name: 'Свой вариант' })
    await userEvent.type(input, 'Инвестиционный комитет')

    const specList = screen.getByRole('list', { name: 'Параметры будущей колоды' })
    expect(within(specList).getByText('Инвестиционный комитет')).toBeInTheDocument()
    expect(within(specList).getByText(/Аргументы и выводы под аудиторию «Инвестиционный комитет»/)).toBeInTheDocument()
  })

  it('updates slide count and focus in the live specification when content_short notes are entered', async () => {
    vi.mocked(useQuestions).mockReturnValue({
      data: {
        ...(questionsFixture.body as unknown as QuestionsResult),
        suggested_brief: {
          ...(questionsFixture.body.suggested_brief as QuestionsResult['suggested_brief']),
          content_fill: 'ask',
        },
      },
      isLoading: false,
      isSuccess: true,
    } as unknown as ReturnType<typeof useQuestions>)

    renderQuestionsPage()

    const shortGroup = screen.getByRole('radiogroup', { name: 'Как поступим с объёмом материала?' })
    const notesOption = within(shortGroup).getByRole('radio', { name: /Я добавлю пару тезисов/ })
    await userEvent.click(notesOption)

    const thesis1 = screen.getByRole('textbox', { name: 'Тезис 1' })
    await userEvent.type(thesis1, 'Окупаемость проекта за 9 месяцев')

    const specList = screen.getByRole('list', { name: 'Параметры будущей колоды' })
    expect(within(specList).getByText('8 слайдов · +1 тезис')).toBeInTheDocument()
    expect(within(specList).getByText('Окупаемость проекта за 9 месяцев')).toBeInTheDocument()
  })
})
