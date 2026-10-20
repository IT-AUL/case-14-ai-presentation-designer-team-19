import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { DeckPlan, SlidePlan } from '@/entities/generation'
import dataPlanResponse from '@/shared/api/mocks/fixtures/assistant/data-plan.json'
import { PlanCard } from './PlanCard'

const dataPlan = dataPlanResponse.body.plans[0]?.deck_plan as DeckPlan
function byId(id: string): SlidePlan {
  const slide = dataPlan.slides.find((item) => item.id === id)
  if (!slide) throw new Error(`no slide ${id}`)
  return slide
}

function setup(id: string, overrides: Partial<React.ComponentProps<typeof PlanCard>> = {}) {
  const handlers = { onOpen: vi.fn(), onTitleChange: vi.fn(), onUnitChange: vi.fn(), onUnitAdd: vi.fn(), onUnitRemove: vi.fn() }
  render(<PlanCard slide={byId(id)} number={3} {...handlers} {...overrides} />)
  return handlers
}

describe('PlanCard', () => {
  it('shows number, role, title, points, chart reasoning and source', () => {
    setup('slide-2')
    const card = screen.getByRole('article', { name: 'Слайд 3' })
    expect(within(card).getByText('03')).toBeInTheDocument()
    expect(within(card).getByText('Данные')).toBeInTheDocument()
    expect(within(card).getByRole('textbox', { name: 'Заголовок слайда 3' })).toHaveValue(byId('slide-2').title_intent)
    expect(within(card).getAllByRole('textbox', { name: /^Пункт \d слайда 3$/ }).length).toBeGreaterThan(0)
    expect(within(card).getByText('График · Линия')).toBeInTheDocument()
    expect(within(card).getByText('Из таблицы')).toBeInTheDocument()
  })

  it('edits the title and a point right on the card', async () => {
    const handlers = setup('slide-2')
    const title = screen.getByRole('textbox', { name: 'Заголовок слайда 3' })
    await userEvent.type(title, '!')
    expect(handlers.onTitleChange).toHaveBeenLastCalledWith(`${byId('slide-2').title_intent}!`)

    await userEvent.type(screen.getByRole('textbox', { name: 'Пункт 1 слайда 3' }), '?')
    expect(handlers.onUnitChange).toHaveBeenCalled()
    expect(handlers.onUnitChange.mock.calls.at(-1)?.[0]).toBeTypeOf('number')
  })

  it('removes a point and opens the details', async () => {
    const handlers = setup('slide-2')
    await userEvent.click(screen.getByRole('button', { name: 'Убрать пункт 1 слайда 3' }))
    expect(handlers.onUnitRemove).toHaveBeenCalledTimes(1)
    await userEvent.click(screen.getByRole('button', { name: 'Подробнее о слайде 3' }))
    expect(handlers.onOpen).toHaveBeenCalledTimes(1)
  })

  it('opens the slide details from the chart preview, by click and by keyboard', async () => {
    const handlers = setup('slide-2')
    const chartPreview = screen.getByRole('button', { name: 'Подробнее о слайде 3: Линия' })
    await userEvent.click(chartPreview)
    expect(handlers.onOpen).toHaveBeenCalledTimes(1)
    chartPreview.focus()
    await userEvent.keyboard('{Enter}')
    expect(handlers.onOpen).toHaveBeenCalledTimes(2)
  })

  it('collapses long point lists behind a link to the details', async () => {
    const slide = byId('slide-1')
    const extra = { ...slide, content_units: [...slide.content_units, { kind: 'bullet', text: 'Пятый' }, { kind: 'bullet', text: 'Шестой' }] }
    const handlers = { onOpen: vi.fn(), onTitleChange: vi.fn(), onUnitChange: vi.fn(), onUnitAdd: vi.fn(), onUnitRemove: vi.fn() }
    render(<PlanCard slide={extra as SlidePlan} number={2} {...handlers} />)
    expect(screen.getAllByRole('textbox', { name: /^Пункт \d слайда 2$/ })).toHaveLength(3)
    await userEvent.click(screen.getByRole('button', { name: /^Ещё \d+ пункт/ }))
    expect(handlers.onOpen).toHaveBeenCalled()
  })

  it('flags an off-topic slide', async () => {
    setup('slide-6', { offTopic: true })
    expect(screen.getByText('Уводит от главной мысли')).toBeInTheDocument()
  })

  it('switches the chart type straight from the card', async () => {
    const onChartPick = vi.fn()
    setup('slide-2', { onChartPick })
    const group = screen.getByRole('group', { name: 'Тип визуала слайда 3' })
    const active = within(group).getByRole('button', { name: 'Линия' })
    expect(active).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(within(group).getByRole('button', { name: 'Столбцы' }))
    expect(onChartPick).toHaveBeenCalledWith('bar')
  })

  it('hides quick chart chips without a pick handler', () => {
    setup('slide-2')
    expect(screen.queryByRole('group', { name: 'Тип визуала слайда 3' })).not.toBeInTheDocument()
  })
})
