import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useProject } from '@/entities/project'
import type { Project } from '@/entities/project'
import { useActiveProviderSession } from '@/entities/provider-session'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { useTemplate } from '@/entities/template'
import { routes } from '@/shared/config'
import { ArrowRight, Button, EmptyState, Icon, ListOrdered, PageHeader, Skeleton } from '@/shared/ui'
import { resolveContent, toContentBrief, validateBrief } from '../lib/brief'
import type { ContentPlan } from '../lib/brief'
import { useBriefDraft } from '../model/draft'
import { useContentUpload, usePlanFirst, useSubmitBrief } from '../model/mutations'
import { BriefForm } from './BriefForm'
import styles from './BriefPage.module.css'
import { ContentPreview } from './ContentPreview'

type UploadPlan = Extract<ContentPlan, { kind: 'upload' }>

function errorMessage(error: unknown): string | null {
  if (!error) return null
  return error instanceof Error ? error.message : String(error)
}

function BriefScreen({ project }: { project: Project }) {
  const navigate = useNavigate()
  const session = useActiveProviderSession()
  const { data: template } = useTemplate(project.template_id)
  const [form, update] = useBriefDraft(project.id, {
    language: project.default_language,
    targetSlideCount: project.target_slide_count,
    contentPackId: project.content_pack_id,
  })
  const [files, setFiles] = useState<File[]>([])
  const [showErrors, setShowErrors] = useState(false)
  const [attempts, setAttempts] = useState(0)
  const upload = useContentUpload(project.id)
  const submit = useSubmitBrief(project.id)
  const planFirst = usePlanFirst(project.id)
  const planOnly = useCapabilityFlag(FEATURE_PATHS.planOnly)

  const plan = resolveContent({ mode: form.contentMode, files, text: form.text, parsed: form.parsed })
  const errors = validateBrief(form, plan)
  const hasErrors = Object.keys(errors).length > 0
  const templateId = project.template_id ?? null
  const busy = upload.isPending || submit.isPending || planFirst.isPending

  const parse = (target: UploadPlan) => {
    upload.mutate({ file: target.file, key: target.key, label: target.label, brief: toContentBrief(form) }, { onSuccess: (parsed) => update({ parsed }) })
  }

  const changeFiles = (next: File[]) => {
    setFiles(next)
    const nextPlan = resolveContent({ mode: 'file', files: next, text: '', parsed: form.parsed })
    if (nextPlan.kind === 'upload') parse(nextPlan)
  }

  const start = () => {
    setShowErrors(true)
    setAttempts((count) => count + 1)
    if (hasErrors || !templateId) return
    submit.mutate(
      { form, files, templateId, providerSessionId: session?.id ?? null, onParsed: (parsed) => update({ parsed }) },
      { onSuccess: (trackingId) => navigate(routes.run(project.id, trackingId)) },
    )
  }

  const startWithPlan = () => {
    setShowErrors(true)
    setAttempts((count) => count + 1)
    if (hasErrors || !templateId) return
    planFirst.mutate(
      { form, files, templateId, providerSessionId: session?.id ?? null, onParsed: (parsed) => update({ parsed }) },
      { onSuccess: () => navigate(routes.planDraft(project.id)) },
    )
  }

  const parseError = errorMessage(upload.error)
  const actionError = errorMessage(submit.error ?? planFirst.error)
  const previewPackId = plan.kind === 'ready' ? plan.packId : (form.parsed?.packId ?? null)
  const previewLabel = plan.kind === 'ready' ? plan.label : (form.parsed?.label ?? null)

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow="Шаг 2 · Бриф и контент"
        title="О чём презентация"
        description="Обязательны назначение и контент."
      />
      <div className={styles.grid}>
        <BriefForm
          form={form}
          onChange={update}
          files={files}
          onFilesChange={changeFiles}
          plan={plan}
          parsing={upload.isPending}
          parseError={parseError}
          onParseText={() => {
            if (plan.kind === 'upload') parse(plan)
          }}
          errors={showErrors ? errors : {}}
          focusSignal={attempts}
        />
        <aside className={styles.aside}>
          <ContentPreview
            packId={previewPackId}
            sourceLabel={previewLabel}
            parsing={upload.isPending}
            stale={plan.kind === 'upload' && Boolean(form.parsed)}
            error={plan.kind === 'upload' ? parseError : null}
          />
          <div className={styles.cta}>
            <Button variant="primary" size="xl" block disabled={busy || !templateId} onClick={start}>
              {submit.isPending ? 'Отправляю…' : upload.isPending ? 'Разбираю контент…' : 'Собрать 3 варианта'}
              {!busy && <Icon as={ArrowRight} />}
            </Button>
            {planOnly && (
              <Button variant="secondary" size="lg" block disabled={busy || !templateId} onClick={startWithPlan}>
                <Icon as={ListOrdered} />
                {planFirst.isPending ? 'Строю план…' : 'Сначала план'}
              </Button>
            )}
            {actionError && (
              <p className={styles.ctaError} role="alert">
                {actionError}
              </p>
            )}
            {showErrors && hasErrors && (
              <p className={styles.ctaError} role="alert">
                {[errors.purpose, errors.content].filter(Boolean).join('. ')}
              </p>
            )}
            {templateId ? (
              <p className={styles.ctaNote}>Шаблон: {template?.filename ?? '…'}</p>
            ) : (
              <p className={styles.ctaNote}>
                Сначала загрузите шаблон — <Link to={routes.template(project.id)}>к шагу «Шаблон»</Link>
              </p>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}

export function BriefPage() {
  const { projectId = '' } = useParams()
  const { data: project, error } = useProject(projectId)

  if (error) {
    return (
      <div className={styles.page}>
        <EmptyState title="Не удалось открыть проект" description={error.message} />
      </div>
    )
  }

  if (!project) {
    return (
      <div className={styles.page} aria-busy="true">
        <Skeleton className={styles.skeletonTitle} />
        <div className={styles.grid}>
          <Skeleton className={styles.skeletonForm} />
          <Skeleton className={styles.skeletonAside} delay={0.1} />
        </div>
      </div>
    )
  }

  return <BriefScreen key={project.id} project={project} />
}
