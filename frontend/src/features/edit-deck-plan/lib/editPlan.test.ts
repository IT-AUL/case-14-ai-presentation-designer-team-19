import { generationFixture } from '@/shared/api/mocks'
import type { DeckPlan } from '@/entities/generation'
import { addSlide, editSummary, isPlanEdited, moveSlide, removeSlide, updateSlide } from './editPlan'

const plan = generationFixture.deck_plan as DeckPlan
const ids = (target: DeckPlan) => [...target.slides].sort((a, b) => a.index - b.index).map((slide) => slide.id)
const [first = '', second = ''] = ids(plan)

describe('editPlan', () => {
  it('moves a slide and keeps indexes contiguous', () => {
    const moved = moveSlide(plan, first, 1)
    expect(ids(moved).slice(0, 2)).toEqual([second, first])
    expect(moved.slides.map((slide) => slide.index)).toEqual(moved.slides.map((_, index) => index))
    expect(moveSlide(plan, first, -1)).toBe(plan)
  })

  it('removes a slide from the plan and its sections', () => {
    const removed = removeSlide(plan, first)
    expect(ids(removed)).not.toContain(first)
    expect(removed.sections?.flatMap((section) => section.slide_ids)).not.toContain(first)
    expect(editSummary(plan, removed)).toMatchObject({ removed: 1, added: 0 })
  })

  it('never removes the last slide', () => {
    const single = { ...plan, slides: plan.slides.slice(0, 1) }
    expect(removeSlide(single, single.slides[0]?.id ?? '')).toBe(single)
  })

  it('edits titles and messages and reports the plan as edited', () => {
    const edited = updateSlide(plan, first, { title_intent: 'Рынок растёт на 30% в год' })
    expect(edited.slides.find((slide) => slide.id === first)?.title_intent).toBe('Рынок растёт на 30% в год')
    expect(isPlanEdited(plan, edited)).toBe(true)
    expect(editSummary(plan, edited).renamed).toBe(1)
    expect(updateSlide(plan, first, {})).toBe(plan)
  })

  it('adds a valid slide with a unique id at the end', () => {
    const added = addSlide(addSlide(plan))
    const extra = ids(added).slice(-2)
    expect(new Set(extra).size).toBe(2)
    expect(added.slides.find((slide) => slide.id === extra[1])).toMatchObject({ purpose: 'custom', density_budget: { level: 'medium' }, index: plan.slides.length + 1 })
    expect(isPlanEdited(plan, moveSlide(moveSlide(plan, first, 1), first, -1))).toBe(false)
  })
})
