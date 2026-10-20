import { describe, expect, it } from 'vitest'
import { progressSnapshot } from './assistantSnapshot'

const base = { projectId: 'p1', hasTemplate: false, hasContent: false, targetSlides: 10, generated: false, repaired: false, exported: false, reaudited: false }

describe('progressSnapshot', () => {
  it('only opens the first step for a new project', () => {
    const snapshot = progressSnapshot(base)
    expect(snapshot.completed).toEqual([])
    expect(Object.keys(snapshot.hrefs)).toEqual(['1'])
  })

  it('opens the plan and style steps once a draft plan exists', () => {
    const snapshot = progressSnapshot({ ...base, hasTemplate: true, hasContent: true, hasPlanDraft: true })
    expect(snapshot.completed).toEqual([1, 4])
    expect(snapshot.hrefs[5]).toBe('/projects/p1/style')
  })

  it('opens polishing only for a finished run', () => {
    const pending = progressSnapshot({ ...base, hasTemplate: true, hasContent: true, runId: 'r1', runPending: true })
    expect(pending.hrefs[7]).toBeUndefined()
    const done = progressSnapshot({ ...base, hasTemplate: true, hasContent: true, runId: 'r1', generated: true })
    expect(done.completed).toContain(6)
    expect(done.hrefs[7]).toBe('/projects/p1/runs/r1/polish')
  })

  it('keeps the style step clickable for a generated run without a local draft', () => {
    const snapshot = progressSnapshot({ ...base, hasTemplate: true, hasContent: true, runId: 'r1', generated: true })
    expect(snapshot.completed).toEqual([1, 4, 5, 6])
    expect(snapshot.hrefs[4]).toBe('/projects/p1/runs/r1/plan')
    expect(snapshot.hrefs[5]).toBe('/projects/p1/style')
  })

  it('opens the draft plan and style steps from the server-side plan artifact', () => {
    const session = { step: 5, completed_steps: [], skipped_steps: [], artifacts: { plan_id: 'plan-1' } } as unknown as Parameters<typeof progressSnapshot>[1]
    const snapshot = progressSnapshot({ ...base, hasTemplate: true, hasContent: true, runId: 'r1', generated: true }, session)
    expect(snapshot.hrefs[4]).toBe('/projects/p1/plan')
    expect(snapshot.hrefs[5]).toBe('/projects/p1/style')
  })

  it('marks polish done for a generated deck with zero blockers', () => {
    const snapshot = progressSnapshot({ ...base, hasTemplate: true, hasContent: true, runId: 'r1', generated: true, polishClear: true })
    expect(snapshot.completed).toContain(7)
  })

  it('takes completed, skipped and current steps from the session', () => {
    const session = { step: 4, completed_steps: [1, 2], skipped_steps: [3] } as unknown as Parameters<typeof progressSnapshot>[1]
    const snapshot = progressSnapshot({ ...base, hasTemplate: true, hasContent: true }, session)
    expect(snapshot.completed).toEqual([1, 2])
    expect(snapshot.skipped).toEqual([3])
    expect(snapshot.current).toBe(4)
    expect(snapshot.hrefs[3]).toBe('/projects/p1/questions')
  })

  it('does not unlock step 3 on step 1 when only a template is uploaded', () => {
    const session = { step: 1, completed_steps: [], skipped_steps: [] } as unknown as Parameters<typeof progressSnapshot>[1]
    const snapshot = progressSnapshot({ ...base, hasTemplate: true, hasContent: false }, session)
    expect(snapshot.hrefs[1]).toBe('/projects/p1/start')
    expect(snapshot.hrefs[2]).toBeUndefined()
    expect(snapshot.hrefs[3]).toBeUndefined()
  })
})
