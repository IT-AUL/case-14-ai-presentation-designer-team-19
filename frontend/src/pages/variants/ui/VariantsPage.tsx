import { useCallback, useMemo } from 'react'
import { Link, useParams, useSearchParams } from 'react-router'
import { orderVariants, STRATEGY_AXIS, useVariants } from '@/entities/variant'
import { VariantBoard, VariantBoardSkeleton } from '@/widgets/variant-board'
import { VariantCompare } from '@/widgets/variant-compare'
import { isApiError } from '@/shared/api'
import { routes } from '@/shared/config'
import { Button, EmptyState, Mono, PageHeader, Segmented, type SegmentedOption } from '@/shared/ui'
import { auditLink, parseView, type VariantsView } from '../model/view'
import styles from './VariantsPage.module.css'

const VIEW_OPTIONS: readonly SegmentedOption<VariantsView>[] = [
  { value: 'cards', label: 'Карточки' },
  { value: 'compare', label: 'По слайдам' },
]

export function VariantsPage() {
  const { projectId = '', runId = '' } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const view = parseView(searchParams.get('view'))
  const variantsQuery = useVariants(runId)
  const variants = useMemo(() => orderVariants(variantsQuery.data ?? []), [variantsQuery.data])

  const auditHref = useCallback(
    (variantId: string, slideNumber?: number) => auditLink(routes.audit(projectId, runId, variantId), slideNumber),
    [projectId, runId],
  )

  const changeView = (next: VariantsView) =>
    setSearchParams(
      (params) => {
        const updated = new URLSearchParams(params)
        if (next === 'cards') updated.delete('view')
        else updated.set('view', next)
        return updated
      },
      { replace: true },
    )

  const error = variantsQuery.error

  return (
    <div className={styles.page}>
      <PageHeader
        eyebrow="Шаг 5 · Три варианта"
        title="Один контент, один шаблон, три стратегии"
        description={STRATEGY_AXIS}
        actions={
          <>
            <Segmented label="Вид сравнения" options={VIEW_OPTIONS} value={view} onChange={changeView} />
            <Link className={styles.secondaryLink} to={routes.plan(projectId, runId)}>
              План колоды
            </Link>
          </>
        }
      />

      {error ? (
        <EmptyState
          title="Не удалось загрузить варианты"
          description={
            <>
              {error.message}
              {isApiError(error) && error.requestId && (
                <>
                  <br />
                  <Mono className={styles.requestId}>request_id: {error.requestId}</Mono>
                </>
              )}
            </>
          }
          actions={
            <Button variant="primary" onClick={() => void variantsQuery.refetch()} disabled={variantsQuery.isFetching}>
              Повторить
            </Button>
          }
        />
      ) : variantsQuery.isPending ? (
        <VariantBoardSkeleton />
      ) : variants.length === 0 ? (
        <EmptyState
          title="Вариантов пока нет"
          description="Сборка ещё идёт."
          actions={
            <Link className={styles.secondaryLink} to={routes.run(projectId, runId)}>
              К генерации
            </Link>
          }
        />
      ) : (
        <>
          <VariantBoard variants={variants} showThumbnails={view === 'cards'} auditHref={auditHref} />
          {view === 'compare' && <VariantCompare variants={variants} auditHref={auditHref} />}
        </>
      )}
    </div>
  )
}
