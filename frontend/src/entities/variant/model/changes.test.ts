import { describe, expect, it } from 'vitest'
import {
  hasPendingVariantChanges,
  isSlideChangeActive,
  latestSlideChange,
  slideChangeNote,
  type SlideChangeItem,
  type VariantChangesSnapshot,
} from './changes'

function makeItem(partial: Partial<SlideChangeItem> & Pick<SlideChangeItem, 'change_id' | 'slide_id' | 'state'>): SlideChangeItem {
  return {
    action: 'shorter',
    base_revision: 0,
    target_revision: 1,
    ...partial,
  }
}

describe('variant change queue selectors', () => {
  it('identifies active changes and pending snapshot', () => {
    const queued = makeItem({ change_id: 'c1', slide_id: 's1', state: 'queued' })
    const running = makeItem({ change_id: 'c2', slide_id: 's1', state: 'running' })
    const completed = makeItem({ change_id: 'c3', slide_id: 's1', state: 'completed' })

    expect(isSlideChangeActive(queued)).toBe(true)
    expect(isSlideChangeActive(running)).toBe(true)
    expect(isSlideChangeActive(completed)).toBe(false)

    const pendingSnapshot: VariantChangesSnapshot = {
      variant_id: 'v1',
      applied_revision: 0,
      accepted_revision: 1,
      items: [completed, queued],
    }
    expect(hasPendingVariantChanges(pendingSnapshot)).toBe(true)

    const settledSnapshot: VariantChangesSnapshot = {
      variant_id: 'v1',
      applied_revision: 1,
      accepted_revision: 1,
      items: [completed],
    }
    expect(hasPendingVariantChanges(settledSnapshot)).toBe(false)
  })

  it('returns the latest active or completed change for matching slide ids and ignores cancelled tail', () => {
    const first = makeItem({ change_id: 'c1', slide_id: 'plan-1', state: 'completed', target_revision: 1 })
    const second = makeItem({ change_id: 'c2', slide_id: 'plan-1', state: 'queued', target_revision: 2 })
    const snapshot: VariantChangesSnapshot = {
      variant_id: 'v1',
      applied_revision: 1,
      accepted_revision: 2,
      items: [first, second],
    }

    expect(latestSlideChange(snapshot, ['slide-1', 'plan-1'])?.change_id).toBe('c2')

    const cancelledSnapshot: VariantChangesSnapshot = {
      variant_id: 'v1',
      applied_revision: 1,
      accepted_revision: 1,
      items: [first, { ...second, state: 'cancelled' }],
    }
    expect(latestSlideChange(cancelledSnapshot, ['slide-1', 'plan-1'])).toBeNull()
  })

  it('derives Russian note for completed change', () => {
    const withBackendNote = makeItem({
      change_id: 'c1',
      slide_id: 's1',
      state: 'completed',
      result: { assistant_note: 'Сделал слайд короче.' },
    })
    expect(slideChangeNote(withBackendNote)).toBe('Сделал слайд короче.')

    const withFallback = makeItem({
      change_id: 'c2',
      slide_id: 's1',
      state: 'completed',
      action: 'punchier',
    })
    expect(slideChangeNote(withFallback)).toBe('Усилил главный вывод.')
  })
})
