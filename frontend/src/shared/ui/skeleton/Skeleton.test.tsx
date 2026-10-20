import { render, screen } from '@testing-library/react'
import {
  ChoiceListSkeleton,
  DeckSpecSkeleton,
  InsightBoardSkeleton,
  PassportSkeleton,
  PlanBoardSkeleton,
  ThreeColSkeleton,
} from './Skeleton'

describe('Skeleton shapes', () => {
  it('renders PassportSkeleton with accessible status label', () => {
    render(<PassportSkeleton label="Читаю шаблон" />)
    expect(screen.getByRole('status', { name: 'Читаю шаблон' })).toBeInTheDocument()
    expect(screen.getByText('Читаю шаблон')).toBeInTheDocument()
  })

  it('renders InsightBoardSkeleton, DeckSpecSkeleton, ChoiceListSkeleton, PlanBoardSkeleton, and ThreeColSkeleton', () => {
    const { container } = render(
      <div>
        <InsightBoardSkeleton />
        <DeckSpecSkeleton />
        <ChoiceListSkeleton count={3} />
        <PlanBoardSkeleton count={3} />
        <ThreeColSkeleton />
      </div>,
    )

    expect(screen.getByRole('status', { name: 'Разбираю материалы' })).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Загружаю спецификацию' })).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Загружаю варианты' })).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Составляю план' })).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Примеряю три стиля' })).toBeInTheDocument()
    expect(container.querySelectorAll('[aria-hidden="true"]').length).toBeGreaterThan(0)
  })
})
