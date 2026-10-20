import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router'
import { CreateProjectDialog } from '@/features/create-project'
import { useProjects } from '@/entities/project'
import { humanError } from '@/shared/api'
import { routes } from '@/shared/config'
import { Button, EmptyState, Icon, Plus, Search, Segmented, X } from '@/shared/ui'
import { ProjectListSkeleton, sortProjects } from '@/widgets/project-list'
import { FILTER_LABEL, FILTER_ORDER, filterCounts, matchesFilter, matchesQuery, projectView, type ProjectFilter } from '../model/projectView'
import { Composer } from './Composer'
import { ContinueStrip } from './ContinueStrip'
import { ProjectRows } from './ProjectRows'
import styles from './ProjectsPage.module.css'

const CONTINUE_COUNT = 4
const CONTINUE_MIN_PROJECTS = 6
const SEARCH_MIN_PROJECTS = 6

export function ProjectsPage() {
  const navigate = useNavigate()
  const projects = useProjects()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<ProjectFilter>('all')

  const list = useMemo(() => sortProjects(projects.data ?? []), [projects.data])
  const counts = useMemo(() => filterCounts(list), [list])
  const filtered = useMemo(() => list.filter((project) => matchesFilter(project, filter) && matchesQuery(project, query)), [list, filter, query])
  const recent = list.length >= CONTINUE_MIN_PROJECTS ? list.filter((project) => projectView(project).filter === 'ready').slice(0, CONTINUE_COUNT) : []
  const filterOptions = FILTER_ORDER.filter((value) => value === 'all' || counts[value] > 0).map((value) => ({
    value,
    label: `${FILTER_LABEL[value]} ${counts[value]}`,
  }))
  const searching = Boolean(query.trim()) || filter !== 'all'

  return (
    <div className={styles.page}>
      <Composer />

      {recent.length > 0 && <ContinueStrip projects={recent} />}

      <section className={styles.all} aria-labelledby="all-projects-title">
        <div className={styles.allHeader}>
          <h2 id="all-projects-title" className={styles.heading}>
            Все проекты
          </h2>
          <Button variant="ghost" size="sm" onClick={() => setDialogOpen(true)}>
            <Icon as={Plus} size={15} />
            Новый проект
          </Button>
        </div>

        {list.length >= SEARCH_MIN_PROJECTS && (
          <div className={styles.toolbar}>
            <Segmented options={filterOptions} value={filter} onChange={setFilter} label="Показать проекты" size="sm" />
            <label className={styles.search}>
              <Icon as={Search} size={15} />
              <input
                type="search"
                className={styles.searchInput}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Найти по названию"
                aria-label="Поиск по проектам"
              />
              {query && (
                <button type="button" className={styles.searchClear} onClick={() => setQuery('')} aria-label="Очистить поиск">
                  <Icon as={X} size={14} />
                </button>
              )}
            </label>
          </div>
        )}

        {projects.isPending ? (
          <ProjectListSkeleton />
        ) : projects.isError ? (
          <EmptyState
            title="Не удалось загрузить проекты"
            description={humanError(projects.error, 'Сервис не ответил.')}
            actions={
              <Button size="lg" onClick={() => projects.refetch()}>
                Повторить
              </Button>
            }
          />
        ) : filtered.length ? (
          <ProjectRows key={`${filter}:${query}`} projects={filtered} />
        ) : searching ? (
          <div className={styles.empty}>
            <p>Ничего не нашлось.</p>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setQuery('')
                setFilter('all')
              }}
            >
              Показать все
            </Button>
          </div>
        ) : (
          <p className={styles.empty}>Здесь появятся ваши презентации. Начните с темы и шаблона выше.</p>
        )}
      </section>

      <CreateProjectDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onCreated={(project) => {
          setDialogOpen(false)
          navigate(routes.start(project.id))
        }}
      />
    </div>
  )
}
