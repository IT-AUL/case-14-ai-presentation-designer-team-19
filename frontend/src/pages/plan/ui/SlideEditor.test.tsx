import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { DeckPlan, SlidePlan } from '@/entities/generation'
import { clearSlideResearchOutcomes, setSlideResearchOutcome } from '@/features/edit-deck-plan'
import planFixture from '@/shared/api/mocks/fixtures/assistant/data-plan.json'
import { SlideEditor } from './SlideEditor'

const deckPlan = planFixture.body.plans[0]?.deck_plan as DeckPlan
const rawSlide = deckPlan.slides[1] as SlidePlan
const baseSlide: SlidePlan = {
  ...rawSlide,
  desired_visual: 'chart',
  content_units: rawSlide.content_units.filter((unit) => unit.kind !== 'chart'),
}

function createClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
}

function renderEditor(overrides: Partial<React.ComponentProps<typeof SlideEditor>> = {}) {
  const client = createClient()
  const props: React.ComponentProps<typeof SlideEditor> = {
    plan: deckPlan,
    slide: baseSlide,
    first: false,
    last: false,
    researching: false,
    canResearch: true,
    contentPack: null,
    onUpdate: vi.fn(),
    onUnitChange: vi.fn(),
    onUnitAdd: vi.fn(),
    onUnitRemove: vi.fn(),
    onMove: vi.fn(),
    onChart: vi.fn(),
    onAction: vi.fn(),
    onResearch: vi.fn(),
    onKeep: vi.fn(),
    ...overrides,
  }
  const view = render(
    <QueryClientProvider client={client}>
      <SlideEditor {...props} />
    </QueryClientProvider>,
  )
  return {
    ...view,
    rerenderEditor: (nextOverrides: Partial<React.ComponentProps<typeof SlideEditor>> = {}) =>
      view.rerender(
        <QueryClientProvider client={client}>
          <SlideEditor {...props} {...nextOverrides} />
        </QueryClientProvider>,
      ),
  }
}

describe('SlideEditor research outcome', () => {
  beforeEach(() => {
    clearSlideResearchOutcomes()
  })

  it('shows the added items summary when research finds new numbers', () => {
    const onResearch = vi.fn()
    const { rerenderEditor } = renderEditor({ onResearch })

    fireEvent.click(screen.getByRole('button', { name: 'Найти цифры' }))
    expect(onResearch).toHaveBeenCalledOnce()

    const updatedSlide: SlidePlan = {
      ...baseSlide,
      content_units: [
        ...baseSlide.content_units,
        { role: 'supporting', kind: 'number', text: 'Москва — 52% новой выручки (+38% г/г)' },
      ],
    }

    rerenderEditor({ slide: updatedSlide, onResearch })

    expect(screen.getByRole('status')).toHaveTextContent('Нашёл и добавил: Москва — 52% новой выручки (+38% г/г)')
  })

  it('shows the empty outcome message when no new numbers are found in materials', () => {
    const onResearch = vi.fn()
    const { rerenderEditor } = renderEditor({ onResearch })

    fireEvent.click(screen.getByRole('button', { name: 'Найти цифры' }))
    rerenderEditor({ researching: true, onResearch })
    rerenderEditor({ researching: false, onResearch })

    expect(screen.getByRole('status')).toHaveTextContent('Новых цифр в материалах не нашлось')
  })

  it('displays the stored outcome from useResearchPlanSlide', () => {
    setSlideResearchOutcome(baseSlide.id, {
      added: ['NPS вырос с 41 до 54 п.'],
      message: 'Нашёл и добавил: NPS вырос с 41 до 54 п.',
    })
    renderEditor()
    expect(screen.getByRole('status')).toHaveTextContent('Нашёл и добавил: NPS вырос с 41 до 54 п.')
  })

  it('renders ChartReasoningBox with ranked alternatives and reasons for a data slide and calls onChart', () => {
    const onChart = vi.fn()
    const dataSlide: SlidePlan = {
      ...(deckPlan.slides.find((item) => item.id === 'slide-2') as SlidePlan),
      chart: null,
    }
    renderEditor({ slide: dataSlide, onChart })

    const box = screen.getByLabelText(/^Почему такой график/)
    expect(box).toHaveTextContent('Почему такой график')
    expect(box).toHaveTextContent('Столбцы: сравнение отдельных периодов по высоте столбцов')
    fireEvent.click(screen.getByRole('radio', { name: 'Столбцы' }))
    expect(onChart).toHaveBeenCalledWith('bar')
  })
})
