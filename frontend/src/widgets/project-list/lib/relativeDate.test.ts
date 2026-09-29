import { formatRelativeDate } from './relativeDate'

const now = new Date(2026, 8, 25, 18, 0)
const iso = (...parts: [number, number, number, number, number]) => new Date(...parts).toISOString()

describe('formatRelativeDate', () => {
  it('uses today and yesterday for recent dates', () => {
    expect(formatRelativeDate(iso(2026, 8, 25, 14, 20), now)).toBe('сегодня, 14:20')
    expect(formatRelativeDate(iso(2026, 8, 24, 18, 41), now)).toBe('вчера, 18:41')
  })

  it('uses a short date for older dates and adds the year across years', () => {
    expect(formatRelativeDate(iso(2026, 8, 23, 11, 2), now)).toMatch(/^23 сент\.?, 11:02$/)
    expect(formatRelativeDate(iso(2025, 11, 30, 9, 0), now)).toMatch(/^30 дек\.? 2025/)
  })

  it('survives a broken timestamp', () => {
    expect(formatRelativeDate('not a date', now)).toBe('—')
  })
})
