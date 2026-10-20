import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { DeckPlan } from '@/entities/generation'
import dataPlanResponse from '@/shared/api/mocks/fixtures/assistant/data-plan.json'
import type { PlanEditor } from './DeckPlanView'
import { DeckPlanView } from './DeckPlanView'

const dataPlan = dataPlanResponse.body.plans[0]?.deck_plan as DeckPlan
const NOOP = () => undefined

function makeEditor(overrides: Partial<PlanEditor> = {}): PlanEditor {
  return {
    onMove: NOOP,
    onRemove: NOOP,
    onUpdate: NOOP,
    onAdd: NOOP,
    onUnitChange: NOOP,
    onUnitAdd: NOOP,
    onUnitRemove: NOOP,
    onUnitMove: NOOP,
    onChartChange: vi.fn(),
    onQuickAction: vi.fn(),
    onKeyMessageChange: vi.fn(),
    onKeepOnTopic: vi.fn(),
    ...overrides,
  }
}

const withOffTopic: DeckPlan = {
  ...dataPlan,
  slides: dataPlan.slides.map((slide) => (slide.id === 'slide-6' ? { ...slide, supports_key_message: false } : slide)),
}

describe('DeckPlanView', () => {
  it('edits the deck key message from the banner', async () => {
    const editor = makeEditor()
    render(<DeckPlanView plan={dataPlan} headings={new Map()} editor={editor} keyMessage="Рост в 2,1 раза за счёт Москвы" />)

    const banner = screen.getByRole('region', { name: 'Главная мысль колоды' })
    await userEvent.click(within(banner).getByRole('button', { name: 'Изменить мысль' }))
    const input = within(banner).getByRole('textbox', { name: 'Текст главной мысли колоды' })
    await userEvent.clear(input)
    await userEvent.type(input, '  Электроника даёт 61% выручки  ')
    await userEvent.keyboard('{Enter}')
    expect(editor.onKeyMessageChange).toHaveBeenCalledWith('Электроника даёт 61% выручки')
  })

  it('cancels key message editing with Escape', async () => {
    const editor = makeEditor()
    render(<DeckPlanView plan={dataPlan} headings={new Map()} editor={editor} keyMessage="Старая мысль" />)

    await userEvent.click(screen.getByRole('button', { name: 'Изменить мысль' }))
    await userEvent.keyboard('{Escape}')
    expect(editor.onKeyMessageChange).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Старая мысль' })).toBeInTheDocument()
  })

  it('shows the chart reasoning with alternatives and switches type in one click', async () => {
    const editor = makeEditor()
    render(<DeckPlanView plan={dataPlan} headings={new Map()} editor={editor} keyMessage={null} />)

    const box = screen.getAllByLabelText(/^Почему такой график/)[0] as HTMLElement
    expect(within(box).getByText(/24 точки по месяцам/)).toBeInTheDocument()
    const options = within(box).getByRole('group', { name: /^Альтернативы графика/ })
    const active = within(options).getByRole('button', { name: '● Линия' })
    expect(active).toHaveAttribute('aria-pressed', 'true')
    await userEvent.click(within(options).getByRole('button', { name: 'Столбцы' }))
    expect(editor.onChartChange).toHaveBeenCalledWith('slide-2', 'bar')
  })

  it('runs quick actions from the slide toolbar', async () => {
    const editor = makeEditor()
    render(<DeckPlanView plan={dataPlan} headings={new Map()} editor={editor} keyMessage={null} />)

    await userEvent.click(screen.getByRole('button', { name: 'Разбить на два слайд 02' }))
    expect(editor.onQuickAction).toHaveBeenCalledWith('slide-1', 'split')
    await userEvent.click(screen.getByRole('button', { name: 'Убрать слайд 02' }))
    expect(editor.onQuickAction).toHaveBeenCalledWith('slide-1', 'remove')
    expect(screen.queryByRole('button', { name: 'Убрать слайд 01' })).toBeDisabled()
  })

  it('dims a slide that misses the key message and offers to drop or keep it', async () => {
    const editor = makeEditor()
    render(<DeckPlanView plan={withOffTopic} headings={new Map()} editor={editor} keyMessage={null} />)

    const row = screen.getByRole('list', { name: 'Слайды плана' }).querySelector('[data-off-topic="true"]')
    expect(row).not.toBeNull()
    const hint = within(row as HTMLElement).getByRole('note')
    expect(hint).toHaveTextContent('Не про главную мысль — убрать?')
    await userEvent.click(within(hint).getByRole('button', { name: 'Оставить' }))
    expect(editor.onKeepOnTopic).toHaveBeenCalledWith('slide-6')
    await userEvent.click(within(hint).getByRole('button', { name: /^Убрать/ }))
    expect(editor.onQuickAction).toHaveBeenCalledWith('slide-6', 'remove')
  })

  it('keeps the plan readable without an editor: no inputs, chart box stays', () => {
    render(<DeckPlanView plan={dataPlan} headings={new Map()} />)

    expect(screen.queryByRole('textbox', { name: /^Заголовок слайда/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('toolbar')).not.toBeInTheDocument()
    expect(screen.getAllByLabelText(/^Почему такой график/).length).toBeGreaterThan(0)
    expect(screen.queryByRole('button', { name: 'Столбцы' })).not.toBeInTheDocument()
  })
})
