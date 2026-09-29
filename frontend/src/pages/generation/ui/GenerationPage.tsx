import { useEffect } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { CancelGenerationButton } from '@/features/cancel-generation'
import { useRerunGeneration } from '@/features/rerun-generation'
import { describeFailure, failureMeta, GenerationProgress, isTrackingLost, RunNotice } from '@/widgets/generation-tracker'
import { isTrackingId, useGenerationTracker, type GenerationPhase, type GenerationTracker } from '@/entities/generation'
import { markEvidence, projectRunId, rememberRun, useProject } from '@/entities/project'
import { useTemplate } from '@/entities/template'
import { routes } from '@/shared/config'
import { ArrowRight, Button, Icon, PageHeader } from '@/shared/ui'
import styles from './GenerationPage.module.css'

const VARIANT_WORD: Record<number, string> = { 1: 'один вариант', 2: 'два варианта', 3: 'три варианта' }

function variantWord(count: number): string {
  return VARIANT_WORD[count] ?? `${count} вариантов`
}

function headline(phase: GenerationPhase, count: number): string {
  switch (phase) {
    case 'submitting':
    case 'running':
      return `Собираем ${variantWord(count)}`
    case 'completed':
      return count === 1 ? 'Вариант готов' : `${variantWord(count).replace(/^./, (letter) => letter.toUpperCase())} готовы`
    case 'failed':
      return 'Генерация не завершилась'
    case 'canceled':
      return 'Генерация отменена'
  }
}

function lead(phase: GenerationPhase, templateName: string | null): string {
  switch (phase) {
    case 'submitting':
    case 'running':
      return templateName ? `Шаблон «${templateName}». Можно уйти с экрана — сборка продолжится.` : 'Можно уйти с экрана — сборка продолжится.'
    case 'completed':
      return 'Выберите колоду, чтобы открыть её в аудите.'
    case 'failed':
      return 'Бриф и файлы сохранены.'
    case 'canceled':
      return 'Бриф и загруженные файлы сохранены.'
  }
}

function auditLink(projectId: string, runId: string, variantId: string, slideNumber?: number): string {
  const path = routes.audit(projectId, runId, variantId)
  return slideNumber ? `${path}?${new URLSearchParams({ slide: String(slideNumber) }).toString()}` : path
}

function RecoveryNotice({ projectId, runId, tracker }: { projectId: string; runId: string; tracker: GenerationTracker }) {
  const { rerun, isPending } = useRerunGeneration(projectId)
  const { phase, generationId, error } = tracker
  const { data: project } = useProject(projectId)

  if (isTrackingLost(error)) {
    const latest = projectRunId(project, projectId)
    const canOpenLatest = latest !== undefined && latest !== runId
    return (
      <RunNotice
        tone="neutral"
        title="Связь с запуском потеряна"
        message={
          <>
            Страница перезагрузилась до ответа сервиса.
            {canOpenLatest ? ' Откройте последний прогон или запустите сборку заново.' : ' Запустите сборку заново.'}
          </>
        }
        actions={
          <>
            {canOpenLatest && (
              <Link className={styles.primaryLink} to={routes.run(projectId, latest)}>
                Открыть последний прогон
              </Link>
            )}
            <Link className={canOpenLatest ? styles.secondaryLink : styles.primaryLink} to={routes.brief(projectId)}>
              Назад к брифу
            </Link>
          </>
        }
      />
    )
  }

  if (tracker.reconnecting && phase === 'running') {
    return (
      <RunNotice
        tone="info"
        title="Нет связи с сервисом — повторяем…"
        message="Сборка на сервере продолжается. Статус обновится сам, как только связь вернётся."
        actions={
          tracker.refetch && (
            <Button size="lg" onClick={tracker.refetch}>
              Проверить сейчас
            </Button>
          )
        }
      />
    )
  }

  if (phase !== 'failed' && phase !== 'canceled') return null

  const failure = phase === 'failed' ? describeFailure(tracker) : null
  const statusUnknown = !tracker.generation && Boolean(tracker.refetch)
  const retry = generationId ? (
    statusUnknown ? (
      <Button variant="primary" size="lg" onClick={tracker.refetch}>
        Повторить
      </Button>
    ) : (
      <Button variant="primary" size="lg" onClick={() => rerun(generationId)} disabled={isPending}>
        {isPending ? 'Запускаем…' : 'Повторить'}
      </Button>
    )
  ) : null

  return (
    <RunNotice
      tone={phase === 'failed' ? 'error' : 'neutral'}
      title={phase === 'failed' ? 'Вёрстка не завершилась' : 'Генерация отменена'}
      message={failure ? failure.message : 'Сборку можно запустить заново с теми же данными.'}
      meta={failure ? failureMeta(failure) : null}
      actions={
        <>
          {retry}
          <Link className={retry ? styles.secondaryLink : styles.primaryLink} to={routes.brief(projectId)}>
            Назад к брифу
          </Link>
        </>
      }
    />
  )
}

export function GenerationPage() {
  const { projectId = '', runId = '' } = useParams()
  const navigate = useNavigate()
  const tracker = useGenerationTracker(runId)
  const { phase, generationId, canCancel } = tracker
  const tracking = isTrackingId(runId)
  const variantCount = tracker.generation?.variants.length || tracker.variantIds.length || 3
  const lost = isTrackingLost(tracker.error)
  const { data: template } = useTemplate(tracker.generation?.template_id)

  useEffect(() => {
    if (!tracking || !generationId) return
    rememberRun(projectId, generationId)
    navigate(routes.run(projectId, generationId), { replace: true })
  }, [tracking, generationId, projectId, navigate])

  useEffect(() => {
    if (phase === 'completed') markEvidence(projectId, 'generated')
  }, [phase, projectId])

  const actions =
    phase === 'submitting' && !generationId ? (
      <CancelGenerationButton generationId={null} />
    ) : phase === 'running' && canCancel && generationId ? (
      <CancelGenerationButton generationId={generationId} onCanceled={() => navigate(routes.brief(projectId))} />
    ) : phase === 'completed' && generationId ? (
      <Link className={styles.secondaryLink} to={routes.variants(projectId, generationId)}>
        Сравнить по слайдам
        <Icon as={ArrowRight} />
      </Link>
    ) : undefined

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow="Шаг 4 · Генерация"
        title={lost ? 'Статус запуска неизвестен' : headline(phase, variantCount)}
        description={lost ? 'Бриф и загруженные файлы сохранены.' : lead(phase, template?.filename ?? null)}
        actions={actions}
      />
      <RecoveryNotice projectId={projectId} runId={runId} tracker={tracker} />
      {!lost && <GenerationProgress tracker={tracker} auditHref={generationId ? (variantId, slideNumber) => auditLink(projectId, generationId, variantId, slideNumber) : undefined} />}
    </div>
  )
}
