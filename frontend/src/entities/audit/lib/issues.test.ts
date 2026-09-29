import { variantFixtures } from '@/shared/api/mocks'
import type { AuditIssue } from '../model/types'
import { canAutoFix, countBySeverity, filterIssues, groupByCategory, groupBySlide, issueSignature, DEFAULT_ISSUE_FILTER } from './issues'

const issues: AuditIssue[] = variantFixtures.balanced.issues

describe('audit issue helpers', () => {
  it('counts real backend issues by severity', () => {
    expect(countBySeverity(issues)).toEqual({ blocker: 0, error: 54, warning: 54, info: 0 })
  })

  it('treats rules without a backend repair handler as not auto-fixable', () => {
    const fontScale = issues.find((issue) => issue.rule_code === 'template.font_scale')
    const overflow = issues.find((issue) => issue.rule_code === 'text.overflow')
    expect(fontScale && canAutoFix(fontScale)).toBe(false)
    expect(overflow && canAutoFix(overflow)).toBe(true)
  })

  it('groups issues by one-based slide number', () => {
    const groups = groupBySlide(issues, () => undefined)
    expect(groups[0]?.label.startsWith('Слайд 01')).toBe(true)
    expect(groups.reduce((sum, group) => sum + group.issues.length, 0)).toBe(issues.length)
  })

  it('groups issues by category in a stable order', () => {
    expect(groupByCategory(issues).map((group) => group.key)).toEqual(['brand', 'layout', 'text'])
  })

  it('filters fixable issues', () => {
    const fixable = filterIssues(issues, { ...DEFAULT_ISSUE_FILTER, fixableOnly: true })
    expect(fixable.every(canAutoFix)).toBe(true)
    expect(fixable.length).toBeGreaterThan(0)
  })

  it('builds a signature that ignores shape order', () => {
    const base = { rule_code: 'text.overflow', slide_index: 2 }
    expect(issueSignature({ ...base, shape_ids: ['2', '1'] })).toBe(issueSignature({ ...base, shape_ids: ['1', '2'] }))
  })
})
