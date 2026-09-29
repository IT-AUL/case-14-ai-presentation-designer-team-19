import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { resolveContent } from '../lib/brief'
import { createBriefForm } from '../model/form'
import type { BriefForm as BriefFormState } from '../model/form'
import { BriefForm } from './BriefForm'

function Harness({ onState }: { onState: (form: BriefFormState) => void }) {
  const [form, setForm] = useState(createBriefForm({ targetSlideCount: 12 }))
  const [files, setFiles] = useState<File[]>([])
  const plan = resolveContent({ mode: form.contentMode, files, text: form.text, parsed: form.parsed })
  onState(form)
  return (
    <BriefForm
      form={form}
      onChange={(patch) => setForm((current) => ({ ...current, ...patch }))}
      files={files}
      onFilesChange={setFiles}
      plan={plan}
      parsing={false}
      parseError={null}
      onParseText={() => undefined}
      errors={{}}
    />
  )
}

describe('BriefForm', () => {
  it('picks a purpose, adjusts the slide count and warns that only one file is parsed', async () => {
    const user = userEvent.setup()
    const seen = { form: createBriefForm() }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { container } = render(
      <QueryClientProvider client={client}>
        <Harness
          onState={(form) => {
            seen.form = form
          }}
        />
      </QueryClientProvider>,
    )

    const purposes = screen.getByRole('radiogroup', { name: 'Назначение' })
    await user.click(within(purposes).getByRole('radio', { name: /Инициатива/ }))
    expect(within(purposes).getByRole('radio', { name: /Инициатива/ })).toHaveAttribute('aria-checked', 'true')
    await user.keyboard('{ArrowRight}')
    expect(seen.form.purpose).toBe('other')
    expect(screen.getByLabelText(/Что за презентация/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Больше' }))
    expect(seen.form.slideCount).toBe(13)

    const input = container.querySelector('input[type="file"]') as HTMLInputElement
    await user.upload(input, [new File(['# a'], 'brief.md'), new File(['x'], 'metrics.xlsx')])
    expect(screen.getByText('brief.md')).toBeInTheDocument()
    expect(screen.getByText(/Не будет учтён: используется только первый файл/)).toBeInTheDocument()

    await user.click(screen.getByRole('radio', { name: 'Вставить текст' }))
    await user.type(screen.getByLabelText('Текст контента'), 'Контент')
    expect(seen.form.text).toBe('Контент')
    expect(screen.getByRole('button', { name: 'Разобрать текст' })).toBeEnabled()
  })

  it('marks the purpose group invalid and focuses its first option after a failed submit', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const form = createBriefForm({ targetSlideCount: 12 })
    const plan = resolveContent({ mode: form.contentMode, files: [], text: form.text, parsed: form.parsed })
    render(
      <QueryClientProvider client={client}>
        <BriefForm
          form={form}
          onChange={() => undefined}
          files={[]}
          onFilesChange={() => undefined}
          plan={plan}
          parsing={false}
          parseError={null}
          onParseText={() => undefined}
          errors={{ purpose: 'Выберите назначение' }}
          focusSignal={1}
        />
      </QueryClientProvider>,
    )

    const purposes = screen.getByRole('radiogroup', { name: 'Назначение' })
    expect(purposes).toHaveAttribute('aria-invalid', 'true')
    expect(purposes).toHaveAccessibleDescription('Выберите назначение')
    expect(within(purposes).getAllByRole('radio')[0]).toHaveFocus()
  })
})
