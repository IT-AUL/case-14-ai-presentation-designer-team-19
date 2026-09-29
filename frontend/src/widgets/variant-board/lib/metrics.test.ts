import { describe, expect, it } from 'vitest'
import { formatPei, issuesHeadline, peiNote, severityBreakdown, styleFidelityScore, validityView } from './metrics'

describe('validityView', () => {
  it('maps the validity flag to a label and tone', () => {
    expect(validityView(true)).toEqual({ label: 'без ошибок', tone: 'ok' })
    expect(validityView(false)).toEqual({ label: 'с ошибками', tone: 'error' })
    expect(validityView(null)).toEqual({ label: 'нет данных', tone: 'muted' })
  })
})

describe('formatPei', () => {
  it('shows the level out of the PEI maximum', () => {
    expect(formatPei(3)).toBe('3/5')
    expect(formatPei(5)).toBe('5/5')
    expect(formatPei(null)).toBe('—')
  })
})

describe('peiNote', () => {
  it('reports raster-only slides first', () => {
    expect(peiNote(4, 1)).toBe('1 слайд только растром')
    expect(peiNote(3, 3)).toBe('3 слайда только растром')
  })

  it('confirms a fully native deck', () => {
    expect(peiNote(5, null)).toBe('всё нативное')
    expect(peiNote(3, 0)).toBe('без растровых слайдов')
  })

  it('stays silent without data', () => {
    expect(peiNote(null, 2)).toBeNull()
    expect(peiNote(3, null)).toBeNull()
  })
})

describe('issuesHeadline', () => {
  it('pluralizes the total in Russian', () => {
    expect(issuesHeadline(1)).toBe('1 проблема')
    expect(issuesHeadline(3)).toBe('3 проблемы')
    expect(issuesHeadline(92)).toBe('92 проблемы')
    expect(issuesHeadline(108)).toBe('108 проблем')
    expect(issuesHeadline(0)).toBe('Проблем нет')
    expect(issuesHeadline(null)).toBe('Проблемы не посчитаны')
  })
})

describe('severityBreakdown', () => {
  const issues = { blocker: 0, error: 46, warning: 46, info: 0, unresolved: 92 }

  it('orders severities from blocker to info when totals agree', () => {
    expect(severityBreakdown(92, issues)).toEqual([
      { severity: 'blocker', count: 0 },
      { severity: 'error', count: 46 },
      { severity: 'warning', count: 46 },
      { severity: 'info', count: 0 },
    ])
  })

  it('hides the breakdown when it belongs to another revision', () => {
    expect(severityBreakdown(74, issues)).toBeNull()
  })

  it('uses the passport alone when the variant has no total', () => {
    expect(severityBreakdown(null, issues)?.map(({ count }) => count)).toEqual([0, 46, 46, 0])
  })

  it('returns null without a passport', () => {
    expect(severityBreakdown(92, null)).toBeNull()
    expect(severityBreakdown(92, undefined)).toBeNull()
  })
})

describe('styleFidelityScore', () => {
  it('uses the variant score, then the passport mean, else nothing', () => {
    expect(styleFidelityScore(0.91, null)).toBe(0.91)
    expect(styleFidelityScore(null, [{ key: 'palette_compliance', value: 1 }, { key: 'font_compliance', value: 0.8 }])).toBeCloseTo(0.9)
    expect(styleFidelityScore(null, [])).toBeNull()
  })
})
