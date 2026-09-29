import { generationFixture } from '@/shared/api/mocks'
import { parsePlanDraft } from './plans'

const plan = generationFixture.deck_plan

describe('parsePlanDraft', () => {
  it('reads the documented per-strategy response', () => {
    expect(parsePlanDraft({ plan_id: 'p1', plans: [{ strategy: 'balanced', deck_plan: plan }] })).toEqual({
      planId: 'p1',
      proposals: [{ strategy: 'balanced', deckPlan: plan }],
    })
  })

  it('accepts a bare deck plan or a single deck_plan field', () => {
    expect(parsePlanDraft(plan).proposals).toHaveLength(1)
    expect(parsePlanDraft({ deck_plan: plan }).proposals[0]?.deckPlan).toBe(plan)
  })

  it('returns no proposals for an unrelated payload', () => {
    expect(parsePlanDraft({ plans: [{ strategy: 'x' }] }).proposals).toEqual([])
  })
})
