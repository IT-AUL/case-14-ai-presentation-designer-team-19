import { defaultProjectName, normalizeProjectName, PROJECT_NAME_MAX } from './projectName'

describe('defaultProjectName', () => {
  it('stamps the prefix with a short Russian date and time', () => {
    const name = defaultProjectName('Новый проект', new Date(2026, 8, 25, 14, 20))
    expect(name).toMatch(/^Новый проект · 25 сент\.?,? 14:20$/)
  })
})

describe('normalizeProjectName', () => {
  it('collapses whitespace and caps the length', () => {
    expect(normalizeProjectName('  Отчёт   по   проекту  ')).toBe('Отчёт по проекту')
    expect(normalizeProjectName('x'.repeat(500))).toHaveLength(PROJECT_NAME_MAX)
    expect(normalizeProjectName('   ')).toBe('')
  })
})
