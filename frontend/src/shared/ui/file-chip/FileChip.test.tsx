import { fireEvent, render, screen } from '@testing-library/react'
import { FileChip } from './FileChip'

describe('FileChip', () => {
  it('shows name, size and kind label', () => {
    render(<FileChip name="corp.pptx" sizeBytes={2 * 1024 * 1024} kind="template" accepted reason="шаблон принят" />)
    expect(screen.getByText('corp.pptx')).toBeInTheDocument()
    expect(screen.getByText('2,0 МБ')).toBeInTheDocument()
    expect(screen.getByText('Шаблон')).toBeInTheDocument()
  })

  it('offers a labelled role select that reports changes', () => {
    const onKindChange = vi.fn()
    render(<FileChip name="data.csv" kind="data" accepted onKindChange={onKindChange} />)
    const select = screen.getByRole('combobox', { name: 'Роль файла data.csv' })
    expect(select).toHaveValue('data')
    fireEvent.change(select, { target: { value: 'brandbook' } })
    expect(onKindChange).toHaveBeenCalledWith('brandbook')
  })

  it('marks rejected files and shows the server reason', () => {
    render(<FileChip name="archive.exe" kind="unknown" accepted={false} reason="формат не поддерживается" />)
    expect(screen.getByRole('alert')).toHaveTextContent('формат не поддерживается')
  })

  it('calls onRemove from the remove button', () => {
    const onRemove = vi.fn()
    render(<FileChip name="archive.exe" kind="unknown" accepted={false} reason="формат не поддерживается" onRemove={onRemove} />)
    screen.getByRole('button', { name: 'Убрать archive.exe' }).click()
    expect(onRemove).toHaveBeenCalledTimes(1)
  })

  it('disables controls while a request is in flight', () => {
    const onKindChange = vi.fn()
    render(<FileChip name="a.pptx" kind="template" accepted disabled onKindChange={onKindChange} />)
    expect(screen.getByRole('combobox', { name: 'Роль файла a.pptx' })).toBeDisabled()
  })
})
