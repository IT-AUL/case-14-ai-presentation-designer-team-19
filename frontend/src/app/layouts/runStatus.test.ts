import type { GenerationDetail } from '@/entities/generation'
import type { ProjectProgress } from '@/widgets/project-rail'
import { ApiError } from '@/shared/api'
import generationFixture from '@/shared/api/mocks/fixtures/generation.json'
import { runStatus } from './runStatus'

const progress: ProjectProgress = {
  projectId: 'p1',
  runId: 'local-1',
  runPending: true,
  hasTemplate: true,
  hasContent: true,
  targetSlides: 12,
  generated: true,
  repaired: false,
  exported: false,
  reaudited: false,
}

const completed = generationFixture as GenerationDetail

describe('runStatus', () => {
  it('reports a rejected submission as an error instead of a running generation', () => {
    const error = new ApiError({ code: 'validation_error', message: 'bad brief', status: 422 })
    expect(runStatus('run', progress, { phase: 'failed', generation: undefined, error })).toEqual({ label: 'Ошибка генерации', tone: 'error' })
  })

  it('does not claim a failure when the tracking id was lost on reload', () => {
    const error = new ApiError({ code: 'tracking_lost', message: 'lost', status: 0 })
    expect(runStatus('run', progress, { phase: 'failed', generation: undefined, error })?.tone).toBe('neutral')
  })

  it('counts the variants the service actually returned', () => {
    const two = { ...completed, variants: completed.variants.slice(0, 2) }
    expect(runStatus('variants', progress, { phase: 'completed', generation: two, error: null })?.label).toBe('Готово · 2 варианта')
  })

  it('shows a running generation while the service works', () => {
    expect(runStatus('run', progress, { phase: 'submitting', generation: undefined, error: null })?.label).toBe('Идёт генерация')
  })

  it('asks for a decision on the audit while critical issues stay open', () => {
    const done = { phase: 'completed', generation: completed, error: null } as const
    expect(runStatus('audit', progress, done, { blocker: 1, error: 53 })).toEqual({ label: 'Ждёт решения: 54 критич.', tone: 'warn' })
    expect(runStatus('export', { ...progress, reaudited: true }, done, { blocker: 0, error: 2 })?.label).toBe('Ждёт решения: 2 критич.')
  })

  it('turns ok on the audit and export once nothing critical is open', () => {
    const done = { phase: 'completed', generation: completed, error: null } as const
    expect(runStatus('audit', { ...progress, reaudited: true }, done, { blocker: 0, error: 0 })).toEqual({ label: 'Повторный аудит пройден', tone: 'ok' })
    expect(runStatus('export', progress, done, { blocker: 0, error: 0 })).toEqual({ label: 'Готово к экспорту', tone: 'ok' })
  })

  it('ignores open counts outside the audit and export steps', () => {
    const done = { phase: 'completed', generation: completed, error: null } as const
    expect(runStatus('variants', progress, done, { blocker: 3, error: 3 })?.label).toBe('Готово · 3 варианта')
    expect(runStatus('audit', progress, done, null)?.label).toBe('Готово · 3 варианта')
  })
})
