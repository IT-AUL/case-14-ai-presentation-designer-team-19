import { describe, expect, it } from 'vitest'
import { assistantStepFromPath, buildAssistantSteps } from './steps'

describe('assistantStepFromPath', () => {
  it.each([
    ['/projects/p1/start', 1],
    ['/projects/p1/template', 1],
    ['/projects/p1/brief', 1],
    ['/projects/p1/understanding', 2],
    ['/projects/p1/questions', 3],
    ['/projects/p1/plan', 4],
    ['/projects/p1/runs/r1/plan', 4],
    ['/projects/p1/style', 5],
    ['/projects/p1/runs/r1', 6],
    ['/projects/p1/runs/r1/variants', 6],
    ['/projects/p1/runs/r1/polish/v1', 7],
    ['/projects/p1/runs/r1/audit', 7],
    ['/projects/p1/runs/r1/export/v2', 7],
  ])('maps %s to step %i', (path, step) => {
    expect(assistantStepFromPath(path)).toBe(step)
  })

  it('returns undefined outside the flow', () => {
    expect(assistantStepFromPath('/')).toBeUndefined()
  })
})

describe('buildAssistantSteps', () => {
  it('marks viewed, completed, skipped and pending steps', () => {
    const steps = buildAssistantSteps({ completed: [1, 2], skipped: [3], current: 4, hrefs: { 1: '/a', 2: '/b' } }, 2)
    expect(steps.map((step) => step.state)).toEqual(['done', 'current', 'skipped', 'todo', 'todo', 'todo', 'todo'])
    expect(steps[0]?.href).toBe('/a')
    expect(steps[2]?.hint).toMatch(/Понял сам/)
    expect(steps[3]?.href).toBeUndefined()
  })

  it('falls back to the session step when the path is outside the flow', () => {
    const steps = buildAssistantSteps({ completed: [], skipped: [], current: 5, hrefs: {} })
    expect(steps.find((step) => step.state === 'current')?.number).toBe(5)
  })
})
