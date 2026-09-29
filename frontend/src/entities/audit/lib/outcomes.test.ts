import { variantFixtures as fixtures } from '@/shared/api/mocks'
import type { AuditIssue } from '../model/types'
import { issueSignature } from './issues'
import { diffRepair, journalActionText, plannedFix } from './journal'
import { parseRepairOutcomes, readCount } from './outcomes'

const issues = fixtures.balanced.issues
const contrast = issues.find((issue) => issue.rule_code === 'accessibility.contrast') as AuditIssue
const overflow = issues.find((issue) => issue.rule_code === 'text.overflow') as AuditIssue

describe('parseRepairOutcomes', () => {
  it('reads outcomes from the job result, the job itself or a JSON string in result_ids', () => {
    const outcome = { issue_id: 'a', status: 'fixed', action: 'map_color', summary: 'Цвет → accent1' }
    expect(parseRepairOutcomes({ result: { outcomes: [outcome] } })).toEqual([
      { issueId: 'a', status: 'fixed', action: 'map_color', summary: 'Цвет → accent1', reason: null },
    ])
    expect(parseRepairOutcomes({ outcomes: [outcome] })?.[0]?.issueId).toBe('a')
    expect(parseRepairOutcomes({ result_ids: { outcomes: JSON.stringify([outcome]) } })?.[0]?.status).toBe('fixed')
  })

  it('returns null when the service reports only counters', () => {
    expect(parseRepairOutcomes({ result_ids: { applied: '3' } })).toBeNull()
  })

  it('normalizes status aliases and drops malformed items', () => {
    const parsed = parseRepairOutcomes({ outcomes: [{ issue_id: 'a', status: 'not_implemented' }, { status: 'fixed' }, { issue_id: 'b', status: 'weird' }] })
    expect(parsed).toEqual([{ issueId: 'a', status: 'skipped', action: null, summary: null, reason: null }])
  })

  it('reads counters as numbers or strings', () => {
    expect(readCount({ result_ids: { applied: '3' } }, 'applied')).toBe(3)
    expect(readCount({ result: { applied: 4 } }, 'applied')).toBe(4)
    expect(readCount({}, 'applied')).toBe(0)
  })
})

describe('diffRepair with service outcomes', () => {
  const snapshot = [contrast, overflow]

  it('uses the per-issue outcome and its reason', () => {
    const entries = diffRepair(snapshot, [], 2, '2026-09-25T10:00:00Z', [
      { issueId: contrast.id, status: 'fixed', action: 'map_color', summary: 'Цвет текста → #FFFFFF', reason: null },
      { issueId: overflow.id, status: 'failed', action: 'shorten_text', summary: null, reason: 'Текст остался длиннее рамки' },
    ])
    expect(entries.map((entry) => entry.outcome)).toEqual(['fixed', 'unresolved'])
    const [fixed, failed] = entries
    expect(fixed && journalActionText(fixed)).toBe('Цвет текста → #FFFFFF')
    expect(failed && journalActionText(failed)).toBe('Не удалось исправить: Текст остался длиннее рамки')
  })

  it('does not trust a reported fix when the issue is still in the new revision', () => {
    const entries = diffRepair([contrast], [{ ...contrast, id: 'next' }], 2, undefined, [
      { issueId: contrast.id, status: 'fixed', action: null, summary: null, reason: null },
    ])
    expect(entries[0]?.outcome).toBe('unresolved')
  })

  it('falls back to the signature diff for issues without an outcome', () => {
    const entries = diffRepair(snapshot, [], 2, undefined, [])
    expect(entries.map((entry) => entry.outcome)).toEqual(['fixed', 'fixed'])
  })
})

describe('stable fingerprints and fix previews', () => {
  it('prefers the service fingerprint over the computed signature', () => {
    expect(issueSignature({ ...contrast, fingerprint: 'fp-1' })).toBe('fp-1')
    expect(issueSignature({ ...contrast, fingerprint: null })).toContain('accessibility.contrast')
  })

  it('shows the fix preview computed by the service first', () => {
    expect(plannedFix({ ...contrast, fix_preview: { description_ru: 'Уменьшить кегль 18 → 14 pt', actions: [] } })).toBe('Уменьшить кегль 18 → 14 pt')
    expect(plannedFix({ ...contrast, fix_preview: { description_ru: ' ', actions: ['map_color'] } })).not.toBe(' ')
  })
})
