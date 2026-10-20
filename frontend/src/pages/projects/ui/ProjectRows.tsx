import { useState } from 'react'
import { Link } from 'react-router'
import { useGeneration } from '@/entities/generation'
import { useDeleteProject, type Project } from '@/entities/project'
import { humanError } from '@/shared/api'
import { pluralize } from '@/shared/lib/format'
import { Icon, Trash2, useToast } from '@/shared/ui'
import { groupByDay, projectView, type ProjectGroup } from '../model/projectView'
import styles from './ProjectRows.module.css'

const PAGE_SIZE = 24

const timeFormat = new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit' })
const dayFormat = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short' })
const yearFormat = new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' })

function rowTime(iso: string, group: ProjectGroup['id']): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  if (group === 'today' || group === 'yesterday') return timeFormat.format(date)
  return date.getFullYear() === new Date().getFullYear() ? dayFormat.format(date) : yearFormat.format(date)
}

function slidesLabel(project: Project, builtSlides: number | null): string {
  if (projectView(project).filter === 'draft') return ''
  const count = builtSlides ?? project.target_slide_count
  return count > 0 ? `${count} ${pluralize(count, ['слайд', 'слайда', 'слайдов'])}` : ''
}

function DeleteControl({ project, confirming, onConfirming }: { project: Project; confirming: boolean; onConfirming: (value: boolean) => void }) {
  const remove = useDeleteProject()
  const toast = useToast()
  const setConfirming = onConfirming

  if (!confirming) {
    return (
      <button type="button" className={styles.delete} aria-label={`Удалить проект «${project.name}»`} onClick={() => setConfirming(true)}>
        <Icon as={Trash2} size={15} />
      </button>
    )
  }

  return (
    <span className={styles.confirm} role="group" aria-label={`Удалить проект «${project.name}»?`}>
      <span className={styles.confirmText}>Удалить?</span>
      <button
        type="button"
        className={styles.confirmYes}
        disabled={remove.isPending}
        onClick={() =>
          remove.mutate(project.id, {
            onError: (error) => {
              setConfirming(false)
              toast.show(humanError(error, 'Не удалось удалить проект'))
            },
          })
        }
      >
        {remove.isPending ? 'Удаляю…' : 'Да'}
      </button>
      <button type="button" className={styles.confirmNo} disabled={remove.isPending} onClick={() => setConfirming(false)}>
        Нет
      </button>
    </span>
  )
}

function ProjectRow({ project, group }: { project: Project; group: ProjectGroup['id'] }) {
  const view = projectView(project)
  const generation = useGeneration(view.filter === 'ready' ? view.runId : null)
  const builtSlides = generation.data?.deck_plan?.slides.length ?? null
  const [confirming, setConfirming] = useState(false)
  return (
    <li className={styles.row} data-confirming={confirming || undefined}>
      <Link to={view.href} className={styles.link}>
        <span className={styles.main}>
          <span className={styles.name}>{project.name}</span>
          {view.hint && <span className={styles.hint}>{view.hint}</span>}
        </span>
        <span className={styles.status}>
          <span className={styles.dot} data-tone={view.tone} aria-hidden />
          {view.label}
        </span>
        <span className={styles.slides}>{slidesLabel(project, builtSlides)}</span>
        <time className={styles.time} dateTime={project.updated_at}>
          {rowTime(project.updated_at, group)}
        </time>
      </Link>
      <DeleteControl project={project} confirming={confirming} onConfirming={setConfirming} />
    </li>
  )
}

export function ProjectRows({ projects }: { projects: readonly Project[] }) {
  const [limit, setLimit] = useState(PAGE_SIZE)
  const visible = projects.slice(0, limit)
  const rest = projects.length - visible.length
  const groups = groupByDay(visible)

  return (
    <div className={styles.wrap}>
      <ul className={styles.list} aria-label="Проекты">
        {groups.map((group) => (
          <li key={group.id} className={styles.group}>
            <h3 className={styles.groupLabel}>
              {group.label}
              <span className={styles.groupCount}>{group.projects.length}</span>
            </h3>
            <ul className={styles.rows} aria-label={group.label}>
              {group.projects.map((project) => (
                <ProjectRow key={project.id} project={project} group={group.id} />
              ))}
            </ul>
          </li>
        ))}
      </ul>
      {rest > 0 && (
        <button type="button" className={styles.more} onClick={() => setLimit((value) => value + PAGE_SIZE)}>
          Показать ещё {Math.min(rest, PAGE_SIZE)} из {rest}
        </button>
      )}
    </div>
  )
}
