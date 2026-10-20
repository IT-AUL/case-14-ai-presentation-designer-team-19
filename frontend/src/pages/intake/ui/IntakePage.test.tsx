import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router'
import { useAssistantSession, useBrandbookSummary, useIntake, useJobEvents, useSetBrandPriority, useSetIntakeKind, useStartAutopilot, useTemplateSummary } from '@/entities/assistant'
import { useContentPack } from '@/entities/content-pack'
import { useInvalidateProject, useProject } from '@/entities/project'
import { useDesignDna, useTemplate } from '@/entities/template'
import { ToastProvider } from '@/shared/ui'
import { IntakePage } from './IntakePage'

vi.mock('@/entities/assistant', () => ({
  useAssistantSession: vi.fn(),
  useBrandbookSummary: vi.fn(),
  useIntake: vi.fn(),
  useJobEvents: vi.fn(),
  useSetBrandPriority: vi.fn(),
  useSetIntakeKind: vi.fn(),
  useStartAutopilot: vi.fn(),
  useTemplateSummary: vi.fn(),
  useUnderstanding: vi.fn(() => ({ data: null, isPending: false })),
}))

vi.mock('@/entities/content-pack', () => ({
  uploadContentPack: vi.fn(),
  useContentPack: vi.fn(),
}))

vi.mock('@/entities/project', () => ({
  useInvalidateProject: vi.fn(),
  useProject: vi.fn(),
}))

vi.mock('@/entities/template', () => ({
  buildDesignSystem: vi.fn(() => null),
  uploadTemplate: vi.fn(),
  useDesignDna: vi.fn(),
  useTemplate: vi.fn(),
}))

vi.mock('../model/useAnalyzeBrandbook', () => ({
  useAnalyzeBrandbook: vi.fn(() => ({ mutate: vi.fn(), isPending: false })),
}))

function renderIntakePage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <MemoryRouter initialEntries={['/projects/prj_1/start']}>
          <Routes>
            <Route path="/projects/:projectId/start" element={<IntakePage />} />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  )
}

describe('IntakePage presets', () => {
  const intakeMutate = vi.fn()

  beforeEach(() => {
    window.localStorage.clear()
    intakeMutate.mockReset()
    vi.mocked(useProject).mockReturnValue({
      data: { id: 'prj_1', name: 'Тест', template_id: 'tpl_1', content_pack_id: null },
      isPending: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useProject>)
    vi.mocked(useInvalidateProject).mockReturnValue(vi.fn())
    vi.mocked(useAssistantSession).mockReturnValue({
      data: { project_id: 'prj_1', step: 1, completed_steps: [], artifacts: { template_id: 'tpl_1', content_pack_id: null }, brief: {} },
    } as unknown as ReturnType<typeof useAssistantSession>)
    vi.mocked(useIntake).mockReturnValue({
      mutate: intakeMutate,
      isPending: false,
      error: null,
    } as unknown as ReturnType<typeof useIntake>)
    vi.mocked(useSetIntakeKind).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useSetIntakeKind>)
    vi.mocked(useStartAutopilot).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useStartAutopilot>)
    vi.mocked(useTemplateSummary).mockReturnValue({ data: null, isPending: false } as unknown as ReturnType<typeof useTemplateSummary>)
    vi.mocked(useBrandbookSummary).mockReturnValue({ data: null, isPending: false } as unknown as ReturnType<typeof useBrandbookSummary>)
    vi.mocked(useContentPack).mockReturnValue({ data: null, isPending: false } as unknown as ReturnType<typeof useContentPack>)
    vi.mocked(useSetBrandPriority).mockReturnValue({ mutate: vi.fn(), isPending: false } as unknown as ReturnType<typeof useSetBrandPriority>)
    vi.mocked(useTemplate).mockReturnValue({ data: null, isPending: false } as unknown as ReturnType<typeof useTemplate>)
    vi.mocked(useDesignDna).mockReturnValue({ data: null, isPending: false } as unknown as ReturnType<typeof useDesignDna>)
    vi.mocked(useJobEvents).mockReturnValue({ events: [], lastEvent: null, done: false, error: null })
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('uploads the selected sample dataset preset and populates the topic in one click', async () => {
    intakeMutate.mockImplementation(
      (
        payload: { files: File[]; idea?: string },
        options: { onSuccess: (data: Record<string, unknown>) => void },
      ) => {
        options.onSuccess({
          project_id: 'prj_1',
          template_id: 'tpl_1',
          content_pack_id: 'pack_1',
          brandbook_id: null,
          assistant_note: 'Принял набор данных.',
          files: payload.files.map((file) => ({
            name: file.name,
            kind: 'data',
            detected_kind: 'data',
            accepted: true,
            reason: null,
            size_bytes: file.size,
          })),
        })
      },
    )

    renderIntakePage()

    const presetBtn = screen.getByRole('button', { name: 'Справочник 240 строк' })
    await userEvent.click(presetBtn)

    expect(intakeMutate).toHaveBeenCalledTimes(1)
    const callArg = intakeMutate.mock.calls[0]?.[0] as { files: File[]; idea?: string }
    expect(callArg.idea).toBe('Каталог тарифных позиций и номенклатуры на 2026 год: сводка по категориям и полный перечень')
    expect(callArg.files.map((file) => file.name)).toEqual(['products_reference.csv'])

    await waitFor(() => {
      expect(screen.getByRole('textbox', { name: 'О чём презентация' })).toHaveValue(
        'Каталог тарифных позиций и номенклатуры на 2026 год: сводка по категориям и полный перечень',
      )
    })
    expect(screen.getByText('products_reference.csv')).toBeInTheDocument()
    expect(presetBtn).toHaveAttribute('aria-pressed', 'true')
  })

  it('uploads the bundled neutral template together with preset data when the project has no template', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, blob: async () => new Blob([new Uint8Array([7, 7])]) })))
    vi.mocked(useProject).mockReturnValue({
      data: { id: 'prj_1', name: 'Тест', template_id: null, content_pack_id: null },
      isPending: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useProject>)
    vi.mocked(useAssistantSession).mockReturnValue({
      data: { project_id: 'prj_1', step: 1, completed_steps: [], artifacts: { template_id: null, content_pack_id: null }, brief: {} },
    } as unknown as ReturnType<typeof useAssistantSession>)
    intakeMutate.mockImplementation(
      (
        payload: { files: File[]; idea?: string },
        options: { onSuccess: (data: Record<string, unknown>) => void },
      ) => {
        options.onSuccess({
          project_id: 'prj_1',
          template_id: 'tpl_new',
          content_pack_id: 'pack_1',
          brandbook_id: null,
          assistant_note: 'Принял шаблон и набор данных.',
          files: payload.files.map((file) => ({
            name: file.name,
            kind: 'data',
            detected_kind: 'data',
            accepted: true,
            reason: null,
            size_bytes: file.size,
          })),
        })
      },
    )

    renderIntakePage()
    await userEvent.click(screen.getByRole('button', { name: 'Выручка и каналы' }))

    await waitFor(() => expect(intakeMutate).toHaveBeenCalledTimes(1))
    const callArg = intakeMutate.mock.calls[0]?.[0] as { files: File[]; idea?: string }
    expect(callArg.files.map((file) => file.name)).toEqual(['neutral-template.pptx', 'sales_monthly.csv', 'regions_comparison.csv', 'channels_share.json'])
  })

  it('explains why next is disabled when the project has no template and no files', () => {
    vi.mocked(useProject).mockReturnValue({
      data: { id: 'prj_1', name: 'Тест', template_id: null, content_pack_id: null },
      isPending: false,
      isError: false,
      error: null,
    } as unknown as ReturnType<typeof useProject>)
    vi.mocked(useAssistantSession).mockReturnValue({
      data: { project_id: 'prj_1', step: 1, completed_steps: [], artifacts: { template_id: null, content_pack_id: null }, brief: {} },
    } as unknown as ReturnType<typeof useAssistantSession>)

    renderIntakePage()

    expect(screen.getByText('Нужен шаблон в формате .pptx или .potx.')).toBeInTheDocument()
  })

  it('shows the reading skeleton while the template is being analyzed', () => {
    vi.mocked(useTemplateSummary).mockReturnValue({ data: null, isPending: true } as unknown as ReturnType<typeof useTemplateSummary>)

    renderIntakePage()

    expect(screen.getByRole('status', { name: 'Читаю шаблон' })).toBeInTheDocument()
    expect(screen.queryByText('Здесь появится паспорт вашего шаблона')).not.toBeInTheDocument()
  })
})
