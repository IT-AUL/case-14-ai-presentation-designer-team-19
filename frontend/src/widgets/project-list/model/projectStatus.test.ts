import { generationFixture, projectFixture } from '@/shared/api/mocks'
import type { GenerationDetail } from '@/entities/generation'
import type { Project } from '@/entities/project'
import { projectHref, projectStatus, sortProjects } from './projectStatus'

const draft: Project = projectFixture
const generation: GenerationDetail = generationFixture
const withTemplate: Project = { ...draft, template_id: 'tpl_1' }
const withContent: Project = { ...withTemplate, content_pack_id: 'cp_1' }

describe('projectStatus', () => {
  it('describes setup progress when there is no run', () => {
    expect(projectStatus(draft, { kind: 'none' })).toEqual({ label: 'Черновик', tone: 'neutral', stage: 'Нужен шаблон' })
    expect(projectStatus(withTemplate, { kind: 'none' })?.stage).toBe('Шаблон разобран · нужен бриф')
    expect(projectStatus(withContent, { kind: 'none' })).toMatchObject({ label: 'Готов к генерации', tone: 'info' })
  })

  it('reads the state of the latest run', () => {
    expect(projectStatus(withContent, { kind: 'ready', generation })).toEqual({ label: 'Готово', tone: 'ok', stage: 'Готово · 3 варианта' })
    expect(projectStatus(withContent, { kind: 'ready', generation: { ...generation, state: 'running' } })).toMatchObject({ label: 'Идёт', tone: 'info' })
    expect(projectStatus(withContent, { kind: 'ready', generation: { ...generation, state: 'failed' } })).toMatchObject({ tone: 'error', stage: 'Ошибка генерации' })
  })

  it('waits while the run is loading and falls back when the run is gone', () => {
    expect(projectStatus(withContent, { kind: 'loading' })).toBeNull()
    expect(projectStatus(withContent, { kind: 'missing' })?.label).toBe('Готов к генерации')
    expect(projectStatus({ ...draft, status: 'archived' }, { kind: 'none' })).toMatchObject({ label: 'В архиве', tone: 'muted' })
  })
})

describe('projectHref', () => {
  it('opens the variants of the latest run, otherwise the template step', () => {
    expect(projectHref(draft, 'gen_1', { kind: 'ready', generation })).toBe(`/projects/${draft.id}/runs/gen_1/variants`)
    expect(projectHref(draft, 'gen_1', { kind: 'missing' })).toBe(`/projects/${draft.id}/template`)
    expect(projectHref(draft, undefined, { kind: 'none' })).toBe(`/projects/${draft.id}/template`)
  })
})

describe('sortProjects', () => {
  it('puts the most recently updated project first', () => {
    const older = { ...draft, id: 'a', updated_at: '2026-09-20T10:00:00Z' }
    const newer = { ...draft, id: 'b', updated_at: '2026-09-25T10:00:00Z' }
    expect(sortProjects([older, newer]).map((project) => project.id)).toEqual(['b', 'a'])
  })
})
