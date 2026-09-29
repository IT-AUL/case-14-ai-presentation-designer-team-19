import { variantFixtures as fixtures } from '@/shared/api/mocks'
import type { AuditIssue } from '../model/types'
import { diffRepair, dismissalEntry, summarizeJournal } from './journal'

const issues = fixtures.balanced.issues

function reissue(issue: AuditIssue, patch: Partial<AuditIssue> = {}): AuditIssue {
  return { ...issue, id: `${issue.id}:next`, deck_revision: 2, shape_ids: [...(issue.shape_ids ?? [])].reverse(), ...patch }
}

describe('diffRepair', () => {
  const overflow = issues.filter((issue) => issue.rule_code === 'text.overflow').slice(0, 2)
  const contrast = issues.find((issue) => issue.rule_code === 'accessibility.contrast') as AuditIssue
  const snapshot = [...overflow, contrast]

  it('marks issues missing from the re-audit as fixed even though ids changed', () => {
    const reaudit = [reissue(contrast)]
    const entries = diffRepair(snapshot, reaudit, 2, '2026-09-25T10:00:00Z')
    expect(entries.map((entry) => entry.outcome)).toEqual(['fixed', 'fixed', 'unresolved'])
    expect(entries.every((entry) => entry.revision === 2)).toBe(true)
  })

  it('treats issues the backend reports as fixed or dismissed as gone', () => {
    const reaudit = [reissue(overflow[0] as AuditIssue, { status: 'fixed' }), reissue(contrast, { status: 'unresolved' })]
    const entries = diffRepair(snapshot, reaudit, 2)
    expect(entries.map((entry) => entry.outcome)).toEqual(['fixed', 'fixed', 'unresolved'])
  })

  it('matches duplicate signatures one to one', () => {
    const twin = { ...contrast, id: 'twin' }
    const entries = diffRepair([contrast, twin], [reissue(contrast)], 3)
    expect(entries.map((entry) => entry.outcome).sort()).toEqual(['fixed', 'unresolved'])
    expect(new Set(entries.map((entry) => entry.id)).size).toBe(2)
  })

  it('summarizes the latest outcome per issue', () => {
    const first = diffRepair([contrast], [reissue(contrast)], 2, '2026-09-25T10:00:00Z')
    const dismissed = dismissalEntry(reissue(contrast), 'Так задумано', 2, '2026-09-25T10:05:00Z')
    expect(summarizeJournal([...first, dismissed])).toEqual({ fixed: 0, unresolved: 0, dismissed: 1 })
    expect(dismissed.reason).toBe('Так задумано')
  })
})
