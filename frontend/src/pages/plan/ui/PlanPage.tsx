import { useEffect, useMemo } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { useRerunGeneration } from '@/features/rerun-generation'
import { DeckPlanView } from '@/widgets/deck-plan'
import { useContentPack } from '@/entities/content-pack'
import { isTrackingId, isTerminalState, useGenerationTracker, type DeckPlan, type GenerationCreate, type GenerationDetail } from '@/entities/generation'
import { useActiveProviderSession } from '@/entities/provider-session'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { STRATEGY_ORDER, strategyInfo, type CatalogStrategy } from '@/entities/variant'
import { routes } from '@/shared/config'
import { ArrowRight, EmptyState, Icon, PageHeader, Skeleton } from '@/shared/ui'
import { EditablePlan } from './EditablePlan'
import styles from './PlanPage.module.css'
import { RebuildDialog } from './RebuildDialog'

const EMPTY_HEADINGS: ReadonlyMap<string, string> = new Map()

const REBUILD_DESCRIPTION = 'Сервис заново соберёт и проверит все варианты по тому же брифу и контенту — это до 5 минут. Текущие варианты останутся в истории проекта.'

function VariantsLink({ to }: { to: string }) {
  return (
    <Link className={styles.primaryLink} to={to}>
      К вариантам
      <Icon as={ArrowRight} />
    </Link>
  )
}

function planOwner(generation: GenerationDetail): string | null {
  const planId = generation.deck_plan?.id ?? generation.deck_plan_id
  const owner = generation.variants.find((variant) => variant.deck_plan_id && variant.deck_plan_id === planId)
  return owner ? strategyInfo(owner.strategy).name : null
}

function hasSeparatePlans(generation: GenerationDetail): boolean {
  const ids = new Set(generation.variants.map((variant) => variant.deck_plan_id).filter(Boolean))
  return ids.size > 1
}

function usesModel(generation: GenerationDetail): boolean {
  return generation.variants.some((variant) => Boolean(variant.planner) && !variant.planner?.includes('deterministic'))
}

function rebuildBody(generation: GenerationDetail, plan: DeckPlan, providerSessionId: string | null): Omit<GenerationCreate, 'deck_plan' | 'deck_plan_id'> {
  const used = STRATEGY_ORDER.filter((strategy) => generation.variants.some((variant) => variant.strategy === strategy))
  const strategies: readonly CatalogStrategy[] = used.length > 0 ? used : STRATEGY_ORDER
  const useLlm = usesModel(generation)
  return {
    template_id: generation.template_id,
    content_pack_id: generation.content_pack_id,
    brief: plan.brief,
    variants: strategies.map((strategy) => ({ strategy })),
    use_llm: useLlm,
    ...(useLlm && providerSessionId ? { provider_session_id: providerSessionId } : {}),
  }
}

export function PlanPage() {
  const { projectId = '', runId = '' } = useParams()
  const navigate = useNavigate()
  const tracker = useGenerationTracker(runId)
  const { generation, generationId, phase } = tracker
  const { rerun, isPending } = useRerunGeneration(projectId)
  const { data: contentPack } = useContentPack(generation?.content_pack_id)
  const planOnly = useCapabilityFlag(FEATURE_PATHS.planOnly)
  const session = useActiveProviderSession()

  const headings = useMemo<ReadonlyMap<string, string>>(
    () => (contentPack ? new Map(contentPack.sections.map((section) => [section.id, section.heading])) : EMPTY_HEADINGS),
    [contentPack],
  )

  useEffect(() => {
    if (isTrackingId(runId) && generationId) navigate(routes.plan(projectId, generationId), { replace: true })
  }, [runId, generationId, projectId, navigate])

  const plan = generation?.deck_plan ?? null
  const settled = generation ? isTerminalState(generation.state) : false
  const canRebuild = Boolean(generationId) && settled && !isPending

  const header = (
    <PageHeader
      eyebrow="Шаг 3 · План"
      title="План колоды"
      description="Порядок слайдов, заголовки-выводы и источники."
      actions={
        plan ? (
          <>
            <RebuildDialog
              label="Пересобрать с теми же данными"
              title="Пересобрать три варианта?"
              description={REBUILD_DESCRIPTION}
              confirmLabel="Пересобрать"
              disabled={!canRebuild}
              pending={isPending}
              onConfirm={() => generationId && rerun(generationId)}
            />
            {settled && generationId && <VariantsLink to={routes.variants(projectId, generationId)} />}
          </>
        ) : undefined
      }
    />
  )

  if (!generation) {
    const waiting = phase === 'submitting' || phase === 'running'
    return (
      <div className={styles.page}>
        {header}
        {waiting ? (
          <EmptyState
            title="План появится после генерации"
            description="План строится вместе с вариантами."
            actions={
              <Link className={styles.secondaryLink} to={routes.run(projectId, runId)}>
                К генерации
              </Link>
            }
          />
        ) : phase === 'failed' ? (
          <EmptyState
            title="План недоступен"
            description={tracker.error?.message ?? 'Сервис не вернул данные прогона.'}
            actions={
              <Link className={styles.secondaryLink} to={routes.brief(projectId)}>
                Назад к брифу
              </Link>
            }
          />
        ) : (
          <div className={styles.skeletons} aria-busy="true" aria-label="Загружаем план">
            {Array.from({ length: 6 }, (_, index) => (
              <Skeleton key={index} className={styles.skeletonRow} delay={index * 0.1} />
            ))}
          </div>
        )}
      </div>
    )
  }

  const owner = planOwner(generation)

  if (planOnly && settled && plan && plan.slides.length > 0) {
    return (
      <EditablePlan
        key={plan.id}
        projectId={projectId}
        draftKey={`run:${generation.id}`}
        basePlan={plan}
        headings={headings}
        eyebrow="Шаг 3 · План"
        buildLabel="Пересобрать по этому плану"
        buildBody={rebuildBody(generation, plan, session?.id ?? null)}
        next={<VariantsLink to={routes.variants(projectId, generation.id)} />}
        confirmDescription={REBUILD_DESCRIPTION}
        notice={owner ? <>План варианта «{owner}».</> : null}
      />
    )
  }

  return (
    <div className={styles.page}>
      {header}
      {owner && hasSeparatePlans(generation) && (
        <div className={styles.banner} role="note">
          <div className={styles.bannerText}>План варианта «{owner}». У остальных вариантов свои планы на том же контенте.</div>
        </div>
      )}
      {plan && plan.slides.length > 0 ? (
        <DeckPlanView plan={plan} headings={headings} />
      ) : (
        <EmptyState
          title="Плана нет"
          description={settled ? 'У этого прогона нет плана колоды.' : 'План появится, когда генерация завершится.'}
          actions={
            <Link className={styles.secondaryLink} to={routes.run(projectId, generation.id)}>
              К генерации
            </Link>
          }
        />
      )}
    </div>
  )
}
