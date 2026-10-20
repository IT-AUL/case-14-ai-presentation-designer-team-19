import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router'
import { useGeneration } from '@/entities/generation'
import { useDeleteProject, type Project } from '@/entities/project'
import { ToastProvider } from '@/shared/ui'
import { ProjectRows } from './ProjectRows'
import type * as GenerationModule from '@/entities/generation'
import type * as ProjectModule from '@/entities/project'

vi.mock('@/entities/project', async (importOriginal) => ({
  ...(await importOriginal<typeof ProjectModule>()),
  useDeleteProject: vi.fn(),
}))

vi.mock('@/entities/generation', async (importOriginal) => ({
  ...(await importOriginal<typeof GenerationModule>()),
  useGeneration: vi.fn(() => ({ data: undefined })),
}))

const now = new Date().toISOString()

function project(id: string, name: string, overrides: Partial<Project> = {}): Project {
  return {
    id,
    name,
    status: 'draft',
    default_language: 'ru',
    target_slide_count: 12,
    template_id: 'tpl_1',
    content_pack_id: 'pack_1',
    latest_run_id: 'run_1',
    latest_run_state: 'completed',
    latest_run_stage: null,
    created_at: now,
    updated_at: now,
    ...overrides,
  } as Project
}

function renderRows(projects: Project[]) {
  return render(
    <ToastProvider>
      <MemoryRouter>
        <ProjectRows projects={projects} />
      </MemoryRouter>
    </ToastProvider>,
  )
}

describe('ProjectRows', () => {
  const mutate = vi.fn()

  beforeEach(() => {
    mutate.mockReset()
    vi.mocked(useDeleteProject).mockReturnValue({ mutate, isPending: false } as unknown as ReturnType<typeof useDeleteProject>)
    vi.mocked(useGeneration).mockReturnValue({ data: undefined } as unknown as ReturnType<typeof useGeneration>)
  })

  it('groups rows by day and links each project to its step', () => {
    renderRows([project('prj_a', 'Демо · VK Tech'), project('prj_b', 'Черновик без шаблона', { template_id: null, content_pack_id: null, latest_run_id: null, latest_run_state: null })])
    expect(screen.getByRole('heading', { name: /Сегодня/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Демо · VK Tech/ })).toHaveAttribute('href', '/projects/prj_a/runs/run_1/polish')
    expect(screen.getByRole('link', { name: /Черновик без шаблона/ })).toHaveTextContent('Нужен шаблон')
    expect(screen.getByRole('link', { name: /Черновик без шаблона/ })).not.toHaveTextContent('слайд')
  })

  it('asks before deleting and deletes on confirmation', async () => {
    renderRows([project('prj_a', 'Старый прогон')])
    await userEvent.click(screen.getByRole('button', { name: 'Удалить проект «Старый прогон»' }))
    await userEvent.click(screen.getByRole('button', { name: 'Нет' }))
    expect(mutate).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Удалить проект «Старый прогон»' }))
    await userEvent.click(screen.getByRole('button', { name: 'Да' }))
    expect(mutate).toHaveBeenCalledWith('prj_a', expect.any(Object))
  })

  it('shows the built slide count for a ready deck instead of the requested one', () => {
    vi.mocked(useGeneration).mockReturnValue({
      data: { deck_plan: { slides: Array.from({ length: 9 }, (_, index) => ({ id: `slide-${index}` })) } },
    } as unknown as ReturnType<typeof useGeneration>)
    renderRows([project('prj_a', 'Демо · VK Tech', { target_slide_count: 12 })])
    const link = screen.getByRole('link', { name: /Демо · VK Tech/ })
    expect(link).toHaveTextContent('9 слайдов')
    expect(link).not.toHaveTextContent('12 слайдов')
  })

  it('keeps the requested slide count while the build result is not loaded', () => {
    vi.mocked(useGeneration).mockReturnValue({ data: undefined } as unknown as ReturnType<typeof useGeneration>)
    renderRows([project('prj_a', 'Собранная колода')])
    expect(screen.getByRole('link', { name: /Собранная колода/ })).toHaveTextContent('12 слайдов')
  })

  it('shows more rows on demand', async () => {
    renderRows(Array.from({ length: 30 }, (_, index) => project(`prj_${index}`, `Проект ${index}`)))
    expect(screen.getAllByRole('link')).toHaveLength(24)
    await userEvent.click(screen.getByRole('button', { name: 'Показать ещё 6 из 6' }))
    expect(screen.getAllByRole('link')).toHaveLength(30)
  })
})
