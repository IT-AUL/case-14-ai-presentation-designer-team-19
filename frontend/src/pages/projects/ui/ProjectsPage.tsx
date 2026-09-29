import { useState } from 'react'
import { useNavigate } from 'react-router'
import { CreateProjectDialog, defaultProjectName } from '@/features/create-project'
import { useCreateProject, useProjects, type Project } from '@/entities/project'
import { isApiError } from '@/shared/api'
import { API_MODE, routes } from '@/shared/config'
import { Button, EmptyState, Icon, PageHeader, Play, Plus, useToast } from '@/shared/ui'
import { ProjectList, ProjectListSkeleton } from '@/widgets/project-list'
import styles from './ProjectsPage.module.css'

const IS_MOCK = API_MODE === 'mock'

const DEMO_DESCRIPTION = 'Готовый прогон: ДНК шаблона, план, три варианта, аудит и экспорт.'

function seededProject(projects: readonly Project[] | undefined): Project | undefined {
  const oldestFirst = [...(projects ?? [])].sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at))
  return oldestFirst.find((project) => project.template_id) ?? oldestFirst[0]
}

export function ProjectsPage() {
  const navigate = useNavigate()
  const toast = useToast()
  const projects = useProjects()
  const createDemo = useCreateProject()
  const [dialogOpen, setDialogOpen] = useState(false)

  const openDemo = () => {
    const seeded = seededProject(projects.data)
    if (seeded) {
      navigate(routes.template(seeded.id))
      return
    }
    createDemo.mutate(defaultProjectName('Демо'), {
      onSuccess: (project) => navigate(routes.template(project.id)),
      onError: (error) => toast.show(isApiError(error) ? error.message : 'Не удалось открыть демо'),
    })
  }

  const list = projects.data ?? []

  return (
    <div className={styles.page}>
      <PageHeader
        title="Проекты"
        description="Шаблон и бриф на входе — три редактируемые колоды с аудитом на выходе."
        actions={
          <Button variant="primary" size="lg" onClick={() => setDialogOpen(true)}>
            <Icon as={Plus} />
            Новый проект
          </Button>
        }
      />

      {IS_MOCK && (
        <button type="button" className={styles.demo} onClick={openDemo} disabled={createDemo.isPending}>
          <span className={styles.demoIcon} aria-hidden>
            <Icon as={Play} size={14} />
          </span>
          <span className={styles.demoText}>
            <span className={styles.demoTitle}>Демо</span>
            <span className={styles.demoDescription}>{DEMO_DESCRIPTION}</span>
          </span>
          <span className={styles.demoAction}>{createDemo.isPending ? 'Открываю…' : 'Открыть'}</span>
        </button>
      )}

      {projects.isPending ? (
        <ProjectListSkeleton />
      ) : projects.isError ? (
        <EmptyState
          title="Не удалось загрузить проекты"
          description={isApiError(projects.error) ? projects.error.message : 'Сервис не ответил.'}
          actions={
            <Button size="lg" onClick={() => projects.refetch()}>
              Повторить
            </Button>
          }
        />
      ) : list.length ? (
        <ProjectList projects={list} />
      ) : (
        <EmptyState
          title="Проектов пока нет"
          description="Начните с корпоративного шаблона или прошлой презентации."
          actions={
            <Button variant="primary" size="lg" onClick={() => setDialogOpen(true)}>
              Создать первый проект
            </Button>
          }
        />
      )}

      <CreateProjectDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onCreated={(project) => {
          setDialogOpen(false)
          navigate(routes.template(project.id))
        }}
      />
    </div>
  )
}
