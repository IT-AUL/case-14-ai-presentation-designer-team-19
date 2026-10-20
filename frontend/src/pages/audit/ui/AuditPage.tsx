import { Navigate, useLocation, useParams, useSearchParams } from 'react-router'
import { useGeneration } from '@/entities/generation'
import { useProject } from '@/entities/project'
import { useTemplate } from '@/entities/template'
import { useVariant, useVariants, useVariantSlides } from '@/entities/variant'
import { AgentChat } from '@/features/agent-chat'
import { AssistantCommandBar } from '@/features/assistant-command'
import { AuditWorkspace } from '@/widgets/audit-workspace'
import { SlideInspector } from '@/widgets/slide-inspector'
import { humanError } from '@/shared/api'
import { routes } from '@/shared/config'
import { EmptyState, Skeleton } from '@/shared/ui'
import { defaultAuditVariant, parseSlideParam } from '../model/defaultVariant'
import styles from './AuditPage.module.css'
import { DeckDownloads } from './DeckDownloads'
import { SlidePanel } from './SlidePanel'

function DefaultVariantRedirect({ projectId, runId }: { projectId: string; runId: string }) {
  const { search } = useLocation()
  const variants = useVariants(runId)

  if (variants.isPending) {
    return (
      <div className={styles.center} aria-busy="true">
        <Skeleton className={styles.placeholder} />
      </div>
    )
  }

  const target = variants.data ? defaultAuditVariant(variants.data) : null
  if (!target) {
    return (
      <div className={styles.center}>
        <EmptyState
          title={variants.isError ? 'Не удалось загрузить презентацию' : 'Презентация ещё не собрана'}
          description={variants.error ? humanError(variants.error) : 'Доводка откроется, когда будет готов хотя бы один стиль.'}
        />
      </div>
    )
  }

  return <Navigate replace to={{ pathname: routes.polish(projectId, runId, target.id), search }} />
}

export function AuditPage() {
  const { projectId = '', runId = '', variantId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const project = useProject(projectId)
  const variant = useVariant(variantId)
  const generation = useGeneration(runId)
  const slides = useVariantSlides(variantId)
  const template = useTemplate(generation.data?.template_id)

  if (!variantId) return <DefaultVariantRedirect projectId={projectId} runId={runId} />

  const changeSlide = (slideNumber: number) =>
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current)
        next.set('slide', String(slideNumber))
        return next
      },
      { replace: true },
    )

  const strategy = variant.data?.strategy ?? 'balanced'
  const slideCount = slides.data?.length ?? generation.data?.deck_plan?.slides.length ?? null

  return (
    <AuditWorkspace
      key={variantId}
      projectId={projectId}
      runId={runId}
      variantId={variantId}
      slide={parseSlideParam(searchParams.get('slide'))}
      onSlideChange={changeSlide}
      variantHref={(id, slideNumber) => {
        const next = new URLSearchParams(searchParams)
        const targetSlide = slideNumber ?? parseSlideParam(searchParams.get('slide'))
        if (targetSlide !== null && targetSlide !== undefined) next.set('slide', String(targetSlide))
        const query = next.toString()
        return `${routes.polish(projectId, runId, id)}${query ? `?${query}` : ''}`
      }}
      renderStage={(stage) => <SlideInspector {...stage} />}
      actions={
        variant.data ? (
          <DeckDownloads
            projectId={projectId}
            projectName={project.data?.name ?? null}
            variant={variant.data}
            templateFile={template.data?.filename ?? null}
            slideCount={slideCount}
          />
        ) : null
      }
      renderSlidePanel={({ slideNumber, slideId, revision }) => (
        <div className={styles.chatPanel}>
          <details className={styles.slideDetails}>
            <summary>О слайде · быстрые правки</summary>
            <SlidePanel key={slideNumber} runId={runId} variantId={variantId} strategy={strategy} slideNumber={slideNumber} />
          </details>
          <div className={styles.chatSlot}>
            <AgentChat projectId={projectId} variantId={variantId} slideId={slideId} slides={slides.data} fallback={<AssistantCommandBar projectId={projectId} variantId={variantId} slideId={slideId} expectedRevision={revision} slides={slides.data} />} />
          </div>
        </div>
      )}
    />
  )
}

