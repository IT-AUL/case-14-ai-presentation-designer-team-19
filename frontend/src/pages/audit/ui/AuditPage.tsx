import { Link, Navigate, useLocation, useParams, useSearchParams } from 'react-router'
import { useVariants } from '@/entities/variant'
import { AuditWorkspace } from '@/widgets/audit-workspace'
import { SlideInspector } from '@/widgets/slide-inspector'
import { routes } from '@/shared/config'
import { EmptyState, Skeleton } from '@/shared/ui'
import { defaultAuditVariant, parseSlideParam } from '../model/defaultVariant'
import styles from './AuditPage.module.css'

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
          title={variants.isError ? 'Не удалось загрузить варианты' : 'У прогона нет вариантов'}
          description={variants.error?.message ?? 'Аудит появится, когда будет готов хотя бы один вариант колоды.'}
          actions={
            <Link className={styles.link} to={routes.variants(projectId, runId)}>
              К вариантам
            </Link>
          }
        />
      </div>
    )
  }

  return <Navigate replace to={{ pathname: routes.audit(projectId, runId, target.id), search }} />
}

export function AuditPage() {
  const { projectId = '', runId = '', variantId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()

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

  return (
    <AuditWorkspace
      key={variantId}
      projectId={projectId}
      runId={runId}
      variantId={variantId}
      slide={parseSlideParam(searchParams.get('slide'))}
      onSlideChange={changeSlide}
      renderStage={(stage) => <SlideInspector {...stage} />}
    />
  )
}
