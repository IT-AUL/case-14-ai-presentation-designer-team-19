import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { StepShell } from './StepShell'

it('keeps step decisions accessible and preserves conversation drafts while the assistant is collapsed', async () => {
  const user = userEvent.setup()
  render(
    <StepShell title="План презентации" canvasLabel="План" canvas={<p>Слайды</p>}
      command={<section aria-label="Помощник"><input aria-label="Сообщение" /></section>}
      actions={<button>Выбрать стиль</button>}>
      <input aria-label="Главная мысль" defaultValue="Рост продаж" />
    </StepShell>,
  )
  await user.type(screen.getByRole('textbox', { name: 'Сообщение' }), 'Убери повторы')
  await user.click(screen.getByRole('button', { name: 'Свернуть помощника' }))
  expect(screen.queryByRole('textbox', { name: 'Сообщение' })).not.toBeInTheDocument()
  expect(screen.getByRole('textbox', { name: 'Главная мысль' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Выбрать стиль' })).toBeVisible()
  expect(screen.getByRole('button', { name: 'Открыть помощника' })).toHaveAttribute('aria-expanded', 'false')
  await user.click(screen.getByRole('button', { name: 'Открыть помощника' }))
  expect(screen.getByRole('textbox', { name: 'Сообщение' })).toHaveValue('Убери повторы')
})

it('shows only the step workspace when a conversation is not available', () => {
  render(<StepShell title="Стиль" canvasLabel="Варианты" canvas={<p>Слайды</p>}><button>Все три</button></StepShell>)
  expect(screen.getByRole('button', { name: 'Все три' })).toBeVisible()
  expect(screen.queryByRole('complementary', { name: 'Панель помощника' })).not.toBeInTheDocument()
})
