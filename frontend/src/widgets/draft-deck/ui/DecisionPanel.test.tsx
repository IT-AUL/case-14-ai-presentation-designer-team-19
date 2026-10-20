import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { Insight, Understanding, UnderstandingDataset } from '@/entities/assistant'
import { DecisionPanel } from './DecisionPanel'

function insight(patch: Partial<Insight> = {}): Insight {
  return {
    id: 'i-1',
    dataset_id: 'ds-1',
    kind: 'fact',
    fact: 'Выручка выросла в 2,1 раза',
    headline: null,
    meaning: null,
    implication: null,
    strength: 0.9,
    confirmed: false,
    needs_confirmation: false,
    evidence: [],
    numbers: [],
    ...patch,
  }
}

function dataset(insights: Insight[]): UnderstandingDataset {
  return {
    id: 'ds-1',
    title: 'Продажи',
    file: 'purchases.csv',
    profile_summary: '8755 строк; 6 колонок',
    task: 'trend',
    task_ru: 'Динамика',
    recommendations: [],
    why: null,
    insights,
  }
}

function understanding(insights: Insight[]): Understanding {
  return {
    content_pack_id: 'pack-1',
    blocks: [],
    summary: { capacity_slides: 6, main_points: [], assistant_note: null },
    datasets: [dataset(insights)],
    brief: {},
    context: null,
    interpretation: null,
    assistant_note: null,
  }
}

describe('DecisionPanel', () => {
  it('shows material counts and progress, and confirms the rest in one click', async () => {
    const user = userEvent.setup()
    const onConfirmAll = vi.fn()
    render(
      <DecisionPanel
        understanding={understanding([insight({ id: 'a', confirmed: true }), insight({ id: 'b' }), insight({ id: 'c' })])}
        onConfirmAll={onConfirmAll}
      />,
    )
    expect(screen.getByText('таблица')).toBeInTheDocument()
    expect(screen.getByText('Отмечено 1 из 3')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Отмечено выводов' })).toHaveAttribute('aria-valuenow', '1')
    await user.click(screen.getByRole('button', { name: 'Отметить все выводы (2)' }))
    expect(onConfirmAll).toHaveBeenCalledTimes(1)
  })

  it('hides the bulk action when everything is confirmed', () => {
    render(<DecisionPanel understanding={understanding([insight({ confirmed: true })])} onConfirmAll={vi.fn()} />)
    expect(screen.getByText('Все выводы отмечены')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Отметить все выводы/ })).not.toBeInTheDocument()
  })
})
