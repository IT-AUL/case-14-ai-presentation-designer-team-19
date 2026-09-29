import { describe, expect, it } from 'vitest'
import { recommendVariant } from './recommend'

const variant = (id: string, strategy: string, status: 'completed' | 'running' | 'failed' = 'completed') => ({ id, strategy, status })

const trio = [variant('f', 'faithful'), variant('b', 'balanced'), variant('v', 'visual')]

describe('recommendVariant', () => {
  it('recommends the variant with the fewest blockers and errors and explains why', () => {
    expect(recommendVariant(trio, { f: 46, b: 54, v: 36 })).toEqual({
      variantId: 'v',
      kind: 'quality',
      reason: 'Меньше всего критичных проблем — 36, у других от 46.',
    })
  })

  it('prefers the default strategy among equally good variants', () => {
    expect(recommendVariant(trio, { f: 3, b: 3, v: 9 })).toEqual({ variantId: 'b', kind: 'quality', reason: 'Меньше всего критичных проблем — 3.' })
  })

  it('takes the first tied variant when the default strategy is not among the best', () => {
    expect(recommendVariant(trio, { f: 2, b: 5, v: 2 })?.variantId).toBe('f')
  })

  it('falls back to the default strategy when counts do not tell variants apart', () => {
    expect(recommendVariant(trio, { f: 0, b: 0, v: 0 })).toEqual({ variantId: 'b', kind: 'default', reason: null })
  })

  it('falls back to the default strategy until every variant has a count', () => {
    expect(recommendVariant(trio, { f: 1, b: 4 })).toEqual({ variantId: 'b', kind: 'default', reason: null })
    expect(recommendVariant(trio, { f: 1, b: 4, v: null })).toEqual({ variantId: 'b', kind: 'default', reason: null })
  })

  it('does not recommend while variants are still being built or have failed', () => {
    const building = [variant('f', 'faithful'), variant('b', 'balanced', 'running'), variant('v', 'visual')]
    expect(recommendVariant(building, { f: 1, b: 4, v: 0 })).toEqual({ variantId: 'b', kind: 'default', reason: null })
  })

  it('returns nothing without a default strategy to fall back on', () => {
    expect(recommendVariant([variant('c', 'custom')], {})).toBeNull()
  })
})
