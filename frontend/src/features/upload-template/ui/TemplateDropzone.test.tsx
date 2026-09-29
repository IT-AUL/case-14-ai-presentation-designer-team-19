import { fireEvent, render, screen } from '@testing-library/react'
import { TemplateDropzone } from './TemplateDropzone'

function drop(files: File[]) {
  fireEvent.drop(screen.getByRole('button', { name: /Перетащите шаблон сюда/ }), { dataTransfer: { files, types: ['Files'] } })
}

describe('TemplateDropzone', () => {
  it('hands a valid dropped template to the caller', () => {
    const onFile = vi.fn()
    render(<TemplateDropzone onFile={onFile} />)
    drop([new File(['PK'], 'brand.potx')])
    expect(onFile).toHaveBeenCalledWith(expect.objectContaining({ name: 'brand.potx' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('explains why a file is rejected', () => {
    const onFile = vi.fn()
    render(<TemplateDropzone onFile={onFile} />)
    drop([new File(['%PDF'], 'deck.pdf')])
    expect(onFile).not.toHaveBeenCalled()
    expect(screen.getByRole('alert')).toHaveTextContent('Нужен файл .pptx или .potx — «deck.pdf» не подойдёт.')
  })

  it('shows an error from the server', () => {
    render(<TemplateDropzone onFile={vi.fn()} error="Сервис недоступен" />)
    expect(screen.getByRole('alert')).toHaveTextContent('Сервис недоступен')
  })
})
