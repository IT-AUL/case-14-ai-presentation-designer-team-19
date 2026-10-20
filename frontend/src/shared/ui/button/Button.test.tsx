import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Button, LinkButton } from './Button'

describe('Button', () => {
  it('renders label and triggers onClick when enabled', async () => {
    const onClick = vi.fn()
    render(<Button onClick={onClick}>Дальше</Button>)

    const btn = screen.getByRole('button', { name: 'Дальше' })
    expect(btn).toBeEnabled()
    expect(btn).not.toHaveAttribute('aria-busy')

    await userEvent.click(btn)
    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('shows pending spinner while preserving accessible name and disabling interaction', async () => {
    const onClick = vi.fn()
    render(
      <Button pending onClick={onClick}>
        Собрать все три
      </Button>,
    )

    const btn = screen.getByRole('button', { name: 'Собрать все три' })
    expect(btn).toBeDisabled()
    expect(btn).toHaveAttribute('aria-busy', 'true')
    expect(btn).toHaveAttribute('data-pending', 'true')

    await userEvent.click(btn)
    expect(onClick).not.toHaveBeenCalled()
  })

  it('renders LinkButton as a secondary small action button', async () => {
    const onClick = vi.fn()
    render(<LinkButton onClick={onClick}>Добавить свой тезис</LinkButton>)

    const btn = screen.getByRole('button', { name: 'Добавить свой тезис' })
    expect(btn).toHaveAttribute('type', 'button')

    await userEvent.click(btn)
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})
