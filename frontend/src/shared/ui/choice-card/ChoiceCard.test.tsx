import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Badge } from '../badge'
import { ChoiceCard } from './ChoiceCard'

describe('ChoiceCard', () => {
  it('renders as a radio button with title, description, badge, and shortcut', async () => {
    const onSelect = vi.fn()
    render(
      <ChoiceCard
        title="Совет директоров"
        description="Коротко о деньгах и рисках"
        checked={false}
        recommended
        badge={<Badge shape="tag">советую</Badge>}
        shortcut={1}
        onSelect={onSelect}
      />,
    )

    const radio = screen.getByRole('radio', { name: /Совет директоров/ })
    expect(radio).toHaveAttribute('aria-checked', 'false')
    expect(radio).toHaveAttribute('data-recommended', 'true')
    expect(screen.getByText('Коротко о деньгах и рисках')).toBeInTheDocument()
    expect(screen.getByText('советую')).toBeInTheDocument()

    await userEvent.click(radio)
    expect(onSelect).toHaveBeenCalledTimes(1)
  })

  it('renders native radio input when radioName is provided', async () => {
    const onSelect = vi.fn()
    render(
      <ChoiceCard
        as="section"
        radioName="style"
        title="Визуальный"
        description="Больше графиков и схем"
        ariaLabel="Визуальный, советую. Больше графиков и схем"
        checked
        recommended
        data-strategy="visual"
        onSelect={onSelect}
      >
        <div>Слайды превью</div>
      </ChoiceCard>,
    )

    const radio = screen.getByRole('radio', { name: /Визуальный, советую/ })
    expect(radio).toBeChecked()
    expect(screen.getByText('Слайды превью')).toBeInTheDocument()
  })
})
