import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ResearchSource, SlidePlan } from '@/entities/generation'
import type { PlanEditor } from './DeckPlanView'
import { SlideContentEditor } from './SlideContentEditor'

const source: ResearchSource = { id: 'src-1', title: 'Отчёт о рынке', url: 'https://www.example.com/report', site: 'example.com', accessedAt: null, facts: 3 }

function slide(patch: Partial<SlidePlan> = {}): SlidePlan {
  return {
    id: 's1',
    index: 0,
    purpose: 'data',
    title_intent: 'Рынок растёт',
    key_message: '',
    evidence_ids: ['pack:meta'],
    density_budget: { level: 'medium' },
    desired_visual: 'none',
    content_units: [
      { role: 'title', kind: 'title', text: 'Рынок растёт' },
      { role: 'bullet', kind: 'bullet', text: 'Из вашего документа', origin: 'user', locked: true } as SlidePlan['content_units'][number],
      { role: 'bullet', kind: 'bullet', text: 'Из поиска', origin: 'web', source_id: 'src-1' } as SlidePlan['content_units'][number],
      { role: 'bullet', kind: 'bullet', text: 'Из модели', origin: 'model' } as SlidePlan['content_units'][number],
    ],
    ...patch,
  }
}

function editor(): PlanEditor {
  return { onMove: vi.fn(), onRemove: vi.fn(), onUpdate: vi.fn(), onAdd: vi.fn(), onUnitChange: vi.fn(), onUnitAdd: vi.fn(), onUnitRemove: vi.fn(), onUnitMove: vi.fn() }
}

describe('SlideContentEditor', () => {
  it('shows points with origin badges, a source link and the lock mark', () => {
    render(<SlideContentEditor slide={slide()} number="01" editor={editor()} sources={new Map([[source.id, source]])} />)
    expect(screen.getAllByRole('textbox')).toHaveLength(3)
    expect(screen.getByText('ваш контент')).toBeInTheDocument()
    expect(screen.getByText('знания модели')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /example\.com/ })).toHaveAttribute('href', source.url)
    expect(screen.getAllByTitle('Ваша формулировка: вёрстка её только сократит')).toHaveLength(1)
  })

  it('sends point edits and visual choices to the editor', async () => {
    const actions = editor()
    render(<SlideContentEditor slide={slide()} number="01" editor={actions} sources={new Map()} />)
    await userEvent.type(screen.getByRole('textbox', { name: 'Пункт 3 слайда 01' }), '!')
    expect(actions.onUnitChange).toHaveBeenCalledWith('s1', 3, 'Из модели!')
    await userEvent.click(screen.getByRole('button', { name: 'Удалить пункт 1' }))
    expect(actions.onUnitRemove).toHaveBeenCalledWith('s1', 1)
    await userEvent.click(screen.getByRole('button', { name: 'Текст' }))
    expect(actions.onUpdate).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: 'Таблица' }))
    expect(actions.onUpdate).toHaveBeenCalledWith('s1', { desired_visual: 'table' })
    await userEvent.click(screen.getByRole('button', { name: /Пункт$/ }))
    expect(actions.onUnitAdd).toHaveBeenCalledWith('s1')
  })

  it('warns when a chart has no numbers and offers a table or a diagram', async () => {
    const actions = editor()
    const { rerender } = render(<SlideContentEditor slide={slide({ desired_visual: 'chart' })} number="01" editor={actions} sources={new Map()} />)
    expect(screen.getByRole('note')).toHaveTextContent('Нет данных для графика')
    await userEvent.click(screen.getByRole('button', { name: 'схему' }))
    expect(actions.onUpdate).toHaveBeenCalledWith('s1', { desired_visual: 'diagram' })
    const withNumbers = slide({ desired_visual: 'chart' })
    withNumbers.content_units = [...withNumbers.content_units, { role: 'bullet', kind: 'bullet', text: 'Рост 30% в год' }]
    rerender(<SlideContentEditor slide={withNumbers} number="01" editor={actions} sources={new Map()} />)
    expect(screen.queryByRole('note')).toBeNull()
  })
})
