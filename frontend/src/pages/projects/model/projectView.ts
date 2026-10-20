import { projectRunId, type Project } from '@/entities/project'
import { routes } from '@/shared/config'
import type { Tone } from '@/shared/ui'

export type ProjectFilter = 'all' | 'ready' | 'active' | 'draft' | 'stopped'

export interface ProjectView {
  filter: Exclude<ProjectFilter, 'all'>
  label: string
  tone: Tone
  hint: string
  href: string
  runId: string | null
}

export const FILTER_LABEL: Record<ProjectFilter, string> = {
  all: 'Все',
  ready: 'Готовые',
  active: 'Собираются',
  draft: 'Черновики',
  stopped: 'Прерванные',
}

export const FILTER_ORDER: readonly ProjectFilter[] = ['all', 'ready', 'active', 'draft', 'stopped']

export function projectView(project: Project): ProjectView {
  const runId = projectRunId(project, project.id) ?? null
  const state = runId ? (project.latest_run_state ?? (project.template_id && project.content_pack_id ? 'completed' : null)) : null
  if (runId && state === 'completed') {
    return { filter: 'ready', label: 'Готова', tone: 'ok', hint: '', href: routes.polish(project.id, runId), runId }
  }
  if (runId && (state === 'running' || state === 'queued')) {
    return { filter: 'active', label: 'Собирается', tone: 'info', hint: 'Слайды появляются по мере сборки', href: routes.run(project.id, runId), runId }
  }
  if (runId && state === 'awaiting_user') {
    return { filter: 'active', label: 'Ждёт решения', tone: 'warn', hint: 'Сборка ждёт вашего ответа', href: routes.run(project.id, runId), runId }
  }
  if (runId && state === 'failed') {
    return { filter: 'stopped', label: 'Прервана', tone: 'error', hint: 'Откройте, чтобы собрать заново', href: routes.style(project.id), runId }
  }
  if (runId && state === 'canceled') {
    return { filter: 'stopped', label: 'Остановлена', tone: 'muted', hint: 'Сборку остановили вручную', href: routes.style(project.id), runId }
  }
  if (project.template_id && project.content_pack_id) {
    return { filter: 'draft', label: 'Черновик', tone: 'neutral', hint: 'Шаблон и материалы на месте', href: routes.start(project.id), runId: null }
  }
  if (project.template_id) {
    return { filter: 'draft', label: 'Черновик', tone: 'neutral', hint: 'Шаблон есть, нужна тема или материалы', href: routes.start(project.id), runId: null }
  }
  return { filter: 'draft', label: 'Черновик', tone: 'neutral', hint: 'Нужен шаблон', href: routes.start(project.id), runId: null }
}

export function filterCounts(projects: readonly Project[]): Record<ProjectFilter, number> {
  const counts: Record<ProjectFilter, number> = { all: projects.length, ready: 0, active: 0, draft: 0, stopped: 0 }
  for (const project of projects) counts[projectView(project).filter] += 1
  return counts
}

export function matchesFilter(project: Project, filter: ProjectFilter): boolean {
  return filter === 'all' || projectView(project).filter === filter
}

export function matchesQuery(project: Project, query: string): boolean {
  const needle = query.trim().toLowerCase()
  if (!needle) return true
  const view = projectView(project)
  return `${project.name} ${view.label} ${view.hint}`.toLowerCase().includes(needle)
}

export interface ProjectGroup {
  id: 'today' | 'yesterday' | 'week' | 'earlier'
  label: string
  projects: Project[]
}

const GROUP_LABEL: Record<ProjectGroup['id'], string> = {
  today: 'Сегодня',
  yesterday: 'Вчера',
  week: 'На этой неделе',
  earlier: 'Раньше',
}

const DAY_MS = 24 * 60 * 60 * 1000

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

function groupId(iso: string, now: Date): ProjectGroup['id'] {
  const time = Date.parse(iso)
  if (Number.isNaN(time)) return 'earlier'
  const days = Math.round((startOfDay(now) - startOfDay(new Date(time))) / DAY_MS)
  if (days <= 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 7) return 'week'
  return 'earlier'
}

export function groupByDay(projects: readonly Project[], now: Date = new Date()): ProjectGroup[] {
  const groups = new Map<ProjectGroup['id'], Project[]>()
  for (const project of projects) {
    const id = groupId(project.updated_at, now)
    groups.set(id, [...(groups.get(id) ?? []), project])
  }
  return (['today', 'yesterday', 'week', 'earlier'] as const)
    .filter((id) => groups.has(id))
    .map((id) => ({ id, label: GROUP_LABEL[id], projects: groups.get(id) ?? [] }))
}

const NAME_LIMIT = 80

export function nameFromTopic(topic: string, files: readonly { name: string }[]): string | null {
  const text = topic.replace(/\s+/g, ' ').trim()
  if (text) {
    if (text.length <= NAME_LIMIT) return text
    const cut = text.slice(0, NAME_LIMIT)
    const space = cut.lastIndexOf(' ')
    return `${(space > NAME_LIMIT / 2 ? cut.slice(0, space) : cut).replace(/[\s,.;:—-]+$/, '')}…`
  }
  const template = files.find((file) => /\.(pptx|potx)$/i.test(file.name))
  return template ? template.name.replace(/\.(pptx|potx)$/i, '') : null
}
