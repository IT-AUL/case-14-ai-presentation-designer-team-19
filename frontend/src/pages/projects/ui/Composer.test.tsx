import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'
import { useCreateProject } from '@/entities/project'
import { readIntakeHandoff } from '@/shared/lib/intake-handoff'
import { Composer } from './Composer'
import type * as ProjectModule from '@/entities/project'

vi.mock('@/entities/project', async (importOriginal) => ({
  ...(await importOriginal<typeof ProjectModule>()),
  useCreateProject: vi.fn(),
}))

function StartProbe() {
  const location = useLocation()
  const handoff = readIntakeHandoff(location.state)
  return (
    <p data-testid="start">
      {location.pathname}|{handoff?.idea}|{handoff?.files.map((file) => file.name).join(',')}
    </p>
  )
}

function renderComposer() {
  const client = new QueryClient()
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<Composer />} />
          <Route path="/projects/:projectId/start" element={<StartProbe />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

describe('Composer', () => {
  const mutate = vi.fn()

  beforeEach(() => {
    mutate.mockReset()
    vi.mocked(useCreateProject).mockReturnValue({ mutate, isPending: false, error: null } as unknown as ReturnType<typeof useCreateProject>)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('keeps the start button disabled until there is a topic or a file', async () => {
    renderComposer()
    const start = screen.getByRole('button', { name: 'Начать' })
    expect(start).toBeDisabled()
    await userEvent.type(screen.getByRole('textbox', { name: 'О чём презентация' }), 'Итоги года')
    expect(start).toBeEnabled()
  })

  it('names the project after the topic and hands topic and files to step 1', async () => {
    mutate.mockImplementation((_name: string, options: { onSuccess: (project: { id: string }) => void }) => options.onSuccess({ id: 'prj_new' }))
    const { container } = renderComposer()
    await userEvent.type(screen.getByRole('textbox', { name: 'О чём презентация' }), 'Итоги продаж за год')
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['x'], 'brand.pptx'), new File(['x'], 'virus.exe')] } })
    expect(screen.getByRole('list', { name: 'Прикреплённые файлы' })).toHaveTextContent('brand.pptxШаблон')
    expect(screen.getByRole('alert')).toHaveTextContent('Не подходит формат: virus.exe')
    await userEvent.click(screen.getByRole('button', { name: 'Начать' }))
    expect(mutate).toHaveBeenCalledWith('Итоги продаж за год', expect.any(Object))
    await waitFor(() => expect(screen.getByTestId('start')).toHaveTextContent('/projects/prj_new/start|Итоги продаж за год|brand.pptx'))
  })

  it('removes an attached file', async () => {
    const { container } = renderComposer()
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['x'], 'sales.csv')] } })
    await userEvent.click(screen.getByRole('button', { name: 'Убрать sales.csv' }))
    expect(screen.queryByRole('list', { name: 'Прикреплённые файлы' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Начать' })).toBeDisabled()
  })

  it('attaches the bundled neutral template on preset click when no template is attached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, blob: async () => new Blob([new Uint8Array([1, 2, 3])]) })))
    renderComposer()

    await userEvent.click(screen.getByRole('button', { name: 'Выручка и каналы' }))

    const fileList = await screen.findByRole('list', { name: 'Прикреплённые файлы' })
    await waitFor(() => expect(fileList).toHaveTextContent('neutral-template.pptxШаблон'))
    expect(fileList).toHaveTextContent('sales_monthly.csvДанные')
  })

  it('keeps working when the bundled template cannot be fetched', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false })))
    renderComposer()

    await userEvent.click(screen.getByRole('button', { name: 'Выручка и каналы' }))

    const fileList = await screen.findByRole('list', { name: 'Прикреплённые файлы' })
    expect(fileList).not.toHaveTextContent('neutral-template.pptx')
    expect(fileList).toHaveTextContent('sales_monthly.csvДанные')
  })

  it('populates topic and sample dataset files when clicking a preset chip without removing an attached template', async () => {
    const { container } = renderComposer()
    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    fireEvent.change(input, { target: { files: [new File(['x'], 'brand.pptx')] } })

    const revenueBtn = screen.getByRole('button', { name: 'Выручка и каналы' })
    await userEvent.click(revenueBtn)

    expect(revenueBtn).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('textbox', { name: 'О чём презентация' })).toHaveValue(
      'Итоги продаж за 24 месяца для совета директоров: динамика выручки, лидеры по регионам и структура каналов привлечения',
    )
    const fileList = screen.getByRole('list', { name: 'Прикреплённые файлы' })
    expect(fileList).toHaveTextContent('brand.pptxШаблон')
    expect(fileList).toHaveTextContent('sales_monthly.csvДанные')
    expect(fileList).toHaveTextContent('regions_comparison.csvДанные')
    expect(fileList).toHaveTextContent('channels_share.jsonДанные')

    const analyticsBtn = screen.getByRole('button', { name: 'Аналитика и пропуски' })
    await userEvent.click(analyticsBtn)

    expect(analyticsBtn).toHaveAttribute('aria-pressed', 'true')
    expect(revenueBtn).toHaveAttribute('aria-pressed', 'false')
    expect(fileList).toHaveTextContent('brand.pptxШаблон')
    expect(fileList).not.toHaveTextContent('sales_monthly.csv')
    expect(fileList).toHaveTextContent('nps_with_gaps.csvДанные')
    expect(fileList).toHaveTextContent('order_values.csvДанные')
    expect(fileList).toHaveTextContent('report_with_table.mdТекст / слайды')
  })
})
