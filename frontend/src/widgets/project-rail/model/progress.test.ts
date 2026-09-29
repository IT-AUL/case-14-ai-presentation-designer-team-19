import { describe, expect, it } from 'vitest'
import { buildProofChain, buildRailSteps, stepFromPath, type ProjectProgress } from './progress'

const base: ProjectProgress = {
  projectId: 'p1',
  hasTemplate: false,
  hasContent: false,
  targetSlides: 12,
  generated: false,
  repaired: false,
  exported: false,
  reaudited: false,
}

describe('buildRailSteps', () => {
  it('locks everything after the template until a template is uploaded', () => {
    const steps = buildRailSteps(base, 'template')
    expect(steps.map((step) => step.state)).toEqual(['current', 'locked', 'locked', 'locked', 'locked', 'locked', 'locked'])
  })

  it('opens the brief once a template exists and run steps once a run exists', () => {
    const steps = buildRailSteps({ ...base, hasTemplate: true, hasContent: true, runId: 'r1', generated: true }, 'variants')
    expect(steps.map((step) => step.state)).toEqual(['done', 'done', 'done', 'done', 'current', 'available', 'available'])
    expect(steps.find((step) => step.id === 'audit')?.href).toBe('/projects/p1/runs/r1/audit')
  })
})

describe('buildRailSteps while the service has not returned a run id', () => {
  it('keeps plan and run reachable but locks screens that need a real run id', () => {
    const steps = buildRailSteps({ ...base, hasTemplate: true, hasContent: true, runId: 'local-1', runPending: true }, 'run')
    expect(steps.map((step) => step.state)).toEqual(['done', 'done', 'available', 'current', 'locked', 'locked', 'locked'])
  })
})

describe('buildProofChain', () => {
  it('reports eight links and marks only proven ones', () => {
    const chain = buildProofChain({ ...base, hasTemplate: true })
    expect(chain).toHaveLength(8)
    expect(chain.filter((link) => link.done).map((link) => link.label)).toEqual(['Неизвестный PPTX', 'Извлечённые правила'])
  })

  it('closes the chain with a re-audit without blockers', () => {
    const chain = buildProofChain({ ...base, hasTemplate: true, reaudited: true })
    expect(chain.at(-1)).toEqual({ label: 'Повторный аудит: 0 блокеров', done: true })
  })
})

describe('stepFromPath', () => {
  it.each([
    ['/projects/p1/template', 'template'],
    ['/projects/p1/brief', 'brief'],
    ['/projects/p1/runs/r1', 'run'],
    ['/projects/p1/runs/r1/plan', 'plan'],
    ['/projects/p1/runs/r1/audit/v2', 'audit'],
    ['/projects/p1/runs/r1/export', 'export'],
    ['/projects/p1', undefined],
  ])('%s → %s', (path, step) => {
    expect(stepFromPath(path)).toBe(step)
  })
})
