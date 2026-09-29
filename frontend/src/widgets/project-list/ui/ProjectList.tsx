import { Link } from 'react-router'
import { useGeneration } from '@/entities/generation'
import { projectRunId, type Project } from '@/entities/project'
import { useTemplate } from '@/entities/template'
import { Badge, Skeleton } from '@/shared/ui'
import { formatRelativeDate } from '../lib/relativeDate'
import { projectHref, projectStatus, sortProjects, type RunLookup } from '../model/projectStatus'
import styles from './ProjectList.module.css'

function useRunLookup(runId: string | undefined): RunLookup {
  const generation = useGeneration(runId)
  if (!runId) return { kind: 'none' }
  if (generation.data) return { kind: 'ready', generation: generation.data }
  if (generation.isError) return { kind: 'missing' }
  return { kind: 'loading' }
}

function TemplateName({ templateId }: { templateId: string | null | undefined }) {
  const template = useTemplate(templateId)
  if (!templateId) return <span className={styles.muted}>шаблон не загружен</span>
  if (template.data) return <span title={template.data.filename}>{template.data.filename}</span>
  if (template.isError) return <span className={styles.muted}>{templateId}</span>
  return <Skeleton className={styles.lineSkeleton} />
}

function ProjectRow({ project }: { project: Project }) {
  const runId = projectRunId(project, project.id)
  const run = useRunLookup(runId)
  const status = projectStatus(project, run)

  return (
    <li>
      <Link to={projectHref(project, runId, run)} className={styles.row}>
        <span className={styles.name}>{project.name}</span>
        <span className={styles.file}>
          <TemplateName templateId={project.template_id} />
        </span>
        <span>
          {status ? (
            <Badge tone={status.tone} dot>
              {status.label}
            </Badge>
          ) : (
            <Skeleton className={styles.badgeSkeleton} />
          )}
        </span>
        <span className={styles.stage}>{status?.stage ?? 'Загружаю прогон…'}</span>
        <time className={styles.date} dateTime={project.updated_at}>
          {formatRelativeDate(project.updated_at)}
        </time>
      </Link>
    </li>
  )
}

function Header() {
  return (
    <div className={styles.header} aria-hidden>
      <span>Проект</span>
      <span>Шаблон</span>
      <span>Статус</span>
      <span>Этап прогона</span>
      <span>Дата</span>
    </div>
  )
}

export function ProjectList({ projects }: { projects: readonly Project[] }) {
  return (
    <div className={styles.table}>
      <Header />
      <ul className={styles.rows} aria-label="Проекты">
        {sortProjects(projects).map((project) => (
          <ProjectRow key={project.id} project={project} />
        ))}
      </ul>
    </div>
  )
}

export function ProjectListSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <div className={styles.table} aria-busy aria-label="Загрузка проектов">
      <Header />
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className={styles.skeletonRow}>
          <Skeleton className={styles.lineSkeleton} delay={index * 0.1} />
          <Skeleton className={styles.lineSkeleton} delay={index * 0.1} />
          <Skeleton className={styles.badgeSkeleton} delay={index * 0.1} />
          <Skeleton className={styles.lineSkeleton} delay={index * 0.1} />
          <Skeleton className={styles.lineSkeleton} delay={index * 0.1} />
        </div>
      ))}
    </div>
  )
}
