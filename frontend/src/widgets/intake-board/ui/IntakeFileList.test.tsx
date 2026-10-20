import { fireEvent, render, screen } from '@testing-library/react'
import type { IntakeFile } from '@/entities/assistant'
import { IntakeFileList } from './IntakeFileList'

const file = (name: string, kind: IntakeFile['kind'], accepted = true, reason: string | null = null): IntakeFile => ({ name, kind, detected_kind: kind, accepted, reason, size_bytes: 1024 })

describe('IntakeFileList', () => {
  it('renders nothing when empty', () => {
    const { container } = render(<IntakeFileList files={[]} onKindChange={vi.fn()} onRemove={vi.fn()} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('lists files with role selects', () => {
    render(<IntakeFileList files={[file('corp.pptx', 'template'), file('sales.csv', 'data')]} onKindChange={vi.fn()} onRemove={vi.fn()} />)
    const list = screen.getByRole('list', { name: 'Загруженные файлы' })
    expect(list).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Роль файла corp.pptx' })).toHaveValue('template')
    expect(screen.getByRole('combobox', { name: 'Роль файла sales.csv' })).toHaveValue('data')
  })

  it('reports kind changes with the file name', () => {
    const onKindChange = vi.fn()
    render(<IntakeFileList files={[file('deck.pdf', 'content')]} onKindChange={onKindChange} onRemove={vi.fn()} />)
    const select = screen.getByRole('combobox', { name: 'Роль файла deck.pdf' })
    fireEvent.change(select, { target: { value: 'brandbook' } })
    expect(onKindChange).toHaveBeenCalledWith('deck.pdf', 'brandbook')
  })

  it('lets the user remove a rejected file', () => {
    const onRemove = vi.fn()
    render(<IntakeFileList files={[file('movie.mkv', 'unknown', false, 'формат не поддерживается')]} onKindChange={vi.fn()} onRemove={onRemove} />)
    screen.getByRole('button', { name: 'Убрать movie.mkv' }).click()
    expect(onRemove).toHaveBeenCalledWith('movie.mkv')
  })

  it('shows pending rows with skeleton badges while uploading', () => {
    render(<IntakeFileList files={[file('corp.pptx', 'template')]} onKindChange={vi.fn()} onRemove={vi.fn()} uploading={[{ name: 'notes.md', size: 10 }]} />)
    expect(screen.getByText('notes.md')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Роль файла corp.pptx' })).toBeInTheDocument()
  })

  it('marks the list busy while a patch is in flight', () => {
    render(<IntakeFileList files={[file('corp.pptx', 'template')]} onKindChange={vi.fn()} onRemove={vi.fn()} pending />)
    expect(screen.getByRole('list', { name: 'Загруженные файлы' })).toHaveAttribute('aria-busy', 'true')
  })

  it('proposes brandbook role for image files (.png, .jpg, .webp) when kind is unknown', () => {
    render(
      <IntakeFileList
        files={[file('palette.png', 'unknown'), file('cover.jpg', 'unknown'), file('guide.webp', 'unknown')]}
        onKindChange={vi.fn()}
        onRemove={vi.fn()}
      />,
    )
    expect(screen.getByRole('combobox', { name: 'Роль файла palette.png' })).toHaveValue('brandbook')
    expect(screen.getByRole('combobox', { name: 'Роль файла cover.jpg' })).toHaveValue('brandbook')
    expect(screen.getByRole('combobox', { name: 'Роль файла guide.webp' })).toHaveValue('brandbook')
  })
})
