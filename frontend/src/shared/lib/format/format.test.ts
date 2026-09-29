import { formatBytes, formatClock, formatShortHash, pluralize } from './format'

describe('format', () => {
  it('formats byte sizes in Russian units', () => {
    expect(formatBytes(512)).toBe('512 Б')
    expect(formatBytes(20_525_772)).toBe('19,6 МБ')
  })

  it('formats clock time', () => {
    expect(formatClock(105)).toBe('1:45')
    expect(formatClock(-3)).toBe('0:00')
  })

  it('shortens hashes', () => {
    expect(formatShortHash('cbbe3aa6a21d23ce')).toBe('cbbe…23ce')
  })

  it('pluralizes Russian nouns', () => {
    const forms = ['проблема', 'проблемы', 'проблем'] as const
    expect(pluralize(1, forms)).toBe('проблема')
    expect(pluralize(3, forms)).toBe('проблемы')
    expect(pluralize(11, forms)).toBe('проблем')
    expect(pluralize(108, forms)).toBe('проблем')
  })
})
