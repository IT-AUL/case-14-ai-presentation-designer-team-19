import fixtures from '@/shared/api/mocks/fixtures/variants.json'
import type { VariantSummary } from './types'
import { DEFAULT_STRATEGY, orderVariants, STRATEGIES, STRATEGY_ORDER, strategyInfo, variantAxes, variantRationale } from './strategies'

const variants = [fixtures.visual.variant, fixtures.faithful.variant, fixtures.balanced.variant] as VariantSummary[]

describe('strategy catalog', () => {
  it('describes every strategy the backend produced', () => {
    for (const variant of variants) {
      const info = strategyInfo(variant.strategy)
      expect(info.id).toBe(variant.strategy)
      expect(info.profile).not.toBeNull()
    }
  })

  it('has complete Russian copy for every catalog strategy', () => {
    for (const strategy of STRATEGY_ORDER) {
      const info = STRATEGIES[strategy]
      expect(info.id).toBe(strategy)
      for (const text of [info.name, info.axis, info.audience, info.rationale]) expect(text).toMatch(/[А-Яа-яЁё]/)
    }
    expect(new Set(STRATEGY_ORDER.map((strategy) => STRATEGIES[strategy].name)).size).toBe(STRATEGY_ORDER.length)
  })

  it('differs along the text and visualisation axis', () => {
    const [first, , last] = STRATEGY_ORDER.map((strategy) => STRATEGIES[strategy].profile)
    expect(first && last && first.text > last.text && first.visuals < last.visuals).toBe(true)
  })

  it('recommends exactly the default strategy', () => {
    expect(STRATEGY_ORDER.filter((strategy) => STRATEGIES[strategy].recommended)).toEqual([DEFAULT_STRATEGY])
  })

  it('falls back for custom or unknown strategies', () => {
    expect(strategyInfo('custom').name).toBe('Своя стратегия')
    expect(strategyInfo('experimental').name).toBe('experimental')
  })

  it('uses the catalog rationale while the backend leaves it empty', () => {
    const [visual] = variants
    expect(visual?.rationale).toBeNull()
    expect(visual && variantRationale(visual)).toBe(STRATEGIES.visual.rationale)
    expect(variantRationale({ strategy: 'visual', rationale: 'Своё объяснение' })).toBe('Своё объяснение')
  })

  it('orders variants along the axis', () => {
    expect(orderVariants([...variants, { strategy: 'custom' }]).map((variant) => variant.strategy)).toEqual(['faithful', 'balanced', 'visual', 'custom'])
  })
})

describe('variantAxes', () => {
  it('prefers the measured axes from the service', () => {
    const axes = variantAxes({ strategy: 'visual', axes: { text_density: 0.2, layout_diversity: 1.4, visualization: 0.9 } })
    expect(axes.map((axis) => [axis.label, axis.value])).toEqual([
      ['Текст', 0.2],
      ['Разнообразие макетов', 1],
      ['Визуализация', 0.9],
    ])
  })

  it('falls back to the strategy profile', () => {
    expect(variantAxes({ strategy: 'balanced', axes: null }).map((axis) => axis.caption)).toEqual(['Текст: 2 из 3', 'Макеты-образцы: 2 из 3', 'Визуализация: 2 из 3'])
    expect(variantAxes({ strategy: 'custom', axes: null })).toEqual([])
  })
})
