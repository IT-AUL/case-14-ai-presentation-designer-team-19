import { describe, expect, it } from 'vitest'
import { auditLink, parseView } from './view'

describe('parseView', () => {
  it('defaults to cards for unknown values', () => {
    expect(parseView(null)).toBe('cards')
    expect(parseView('grid')).toBe('cards')
    expect(parseView('compare')).toBe('compare')
  })
})

describe('auditLink', () => {
  it('adds a one-based slide number to the audit path', () => {
    expect(auditLink('/projects/p/runs/r/audit/v', 3)).toBe('/projects/p/runs/r/audit/v?slide=3')
  })

  it('ignores missing or invalid slide numbers', () => {
    expect(auditLink('/a')).toBe('/a')
    expect(auditLink('/a', 0)).toBe('/a')
    expect(auditLink('/a', 1.5)).toBe('/a')
  })
})
