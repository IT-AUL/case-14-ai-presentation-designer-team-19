import type { Project } from '@/entities/project'
import { filterCounts, groupByDay, matchesQuery, nameFromTopic, projectView } from './projectView'

function project(overrides: Partial<Project> = {}): Project {
  return {
    id: 'prj_1',
    name: 'Итоги года',
    status: 'draft',
    default_language: 'ru',
    target_slide_count: 12,
    template_id: null,
    content_pack_id: null,
    latest_run_id: null,
    latest_run_state: null,
    latest_run_stage: null,
    created_at: '2026-10-18T10:00:00Z',
    updated_at: '2026-10-18T10:00:00Z',
    ...overrides,
  } as Project
}

describe('projectView', () => {
  it('opens the studio for a finished deck and the build for a running one', () => {
    expect(projectView(project({ latest_run_id: 'run_1', latest_run_state: 'completed' }))).toMatchObject({
      filter: 'ready',
      label: 'Готова',
      href: '/projects/prj_1/runs/run_1/polish',
      runId: 'run_1',
    })
    expect(projectView(project({ latest_run_id: 'run_1', latest_run_state: 'running' }))).toMatchObject({ filter: 'active', href: '/projects/prj_1/runs/run_1' })
  })

  it('sends an interrupted build back to the style step to rebuild', () => {
    expect(projectView(project({ latest_run_id: 'run_1', latest_run_state: 'failed' }))).toMatchObject({
      filter: 'stopped',
      label: 'Прервана',
      href: '/projects/prj_1/style',
    })
  })

  it('tells a draft what it still needs', () => {
    expect(projectView(project()).hint).toBe('Нужен шаблон')
    expect(projectView(project({ template_id: 'tpl_1' })).hint).toBe('Шаблон есть, нужна тема или материалы')
    expect(projectView(project({ template_id: 'tpl_1', content_pack_id: 'pack_1' })).href).toBe('/projects/prj_1/start')
  })
})

describe('project list helpers', () => {
  it('counts projects by filter', () => {
    const list = [
      project({ id: 'a', latest_run_id: 'r', latest_run_state: 'completed' }),
      project({ id: 'b', latest_run_id: 'r', latest_run_state: 'failed' }),
      project({ id: 'c' }),
    ]
    expect(filterCounts(list)).toEqual({ all: 3, ready: 1, active: 0, draft: 1, stopped: 1 })
  })

  it('groups by day relative to now', () => {
    const now = new Date('2026-10-18T18:00:00')
    const groups = groupByDay(
      [
        project({ id: 'a', updated_at: '2026-10-18T09:00:00' }),
        project({ id: 'b', updated_at: '2026-10-17T09:00:00' }),
        project({ id: 'c', updated_at: '2026-10-14T09:00:00' }),
        project({ id: 'd', updated_at: '2026-09-25T09:00:00' }),
      ],
      now,
    )
    expect(groups.map((group) => [group.label, group.projects.map((item) => item.id)])).toEqual([
      ['Сегодня', ['a']],
      ['Вчера', ['b']],
      ['На этой неделе', ['c']],
      ['Раньше', ['d']],
    ])
  })

  it('searches by name and status', () => {
    expect(matchesQuery(project({ name: 'Демо · VK Tech' }), 'vk tech')).toBe(true)
    expect(matchesQuery(project({ latest_run_id: 'r', latest_run_state: 'failed' }), 'прерван')).toBe(true)
    expect(matchesQuery(project(), 'бюджет')).toBe(false)
  })

  it('names a project after its topic or template', () => {
    expect(nameFromTopic('  Итоги   продаж за год ', [])).toBe('Итоги продаж за год')
    expect(nameFromTopic('', [{ name: 'VK Tech шаблон.pptx' }])).toBe('VK Tech шаблон')
    expect(nameFromTopic('', [{ name: 'sales.csv' }])).toBeNull()
    const long = nameFromTopic('Итоги продаж за два года для совета директоров с разбором по регионам, каналам и категориям товаров', [])
    expect(long?.endsWith('…')).toBe(true)
    expect(long?.length ?? 0).toBeLessThanOrEqual(81)
  })
})
