import { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import {
  assistantKeys,
  useAssistantSession,
  useJobEvents,
  useStartAutopilot,
  useStartStylePreviews,
  useStylePreview,
  type PlanSet,
} from '@/entities/assistant'
import { startGeneration, whenGenerationAccepted, type DeckPlan } from '@/entities/generation'
import { rememberRun, useProject } from '@/entities/project'
import { useDesignDna } from '@/entities/template'
import { DEFAULT_STRATEGY, STRATEGY_ORDER, type CatalogStrategy } from '@/entities/variant'
import { clearPlanDraft, fetchAndStorePlan, readPlanRequest, requestAndStorePlan, usePlanningExecution, type StoredPlanRequest } from '@/features/edit-deck-plan'
import { routes } from '@/shared/config'
import { readJson } from '@/shared/lib/storage'
import { Badge, Button, ChoiceCard, Skeleton } from '@/shared/ui'
import { formatIndex } from '@/shared/lib/format'
import { StepShell } from '@/widgets/step-shell'
import {
  BUILD_MODE_OPTIONS,
  buildGenerationPayload,
  buildStyleColumns,
  resolveRecommendedStrategy,
  resolveStyleAssistantNote,
  type BuildExecutionMode,
} from '../model/styleView'
import { StyleCompareDialog } from './StyleCompareDialog'
import styles from './StylePage.module.css'

const DRAFT_STORAGE_PREFIX = 'deckdna.plan-draft.v1:'

function readEditedPlan(projectId: string, basePlan: DeckPlan | null): { plan: DeckPlan | null; draftKey: string | null } {
  if (!basePlan) return { plan: null, draftKey: null }
  const draftKey = `draft:${projectId}:${basePlan.id}`
  const stored = readJson<{ baseId?: string; plan?: DeckPlan } | null>(`${DRAFT_STORAGE_PREFIX}${draftKey}`, null)
  if (stored && stored.baseId === basePlan.id && Array.isArray(stored.plan?.slides)) {
    return { plan: stored.plan, draftKey }
  }
  return { plan: basePlan, draftKey }
}

function selectProposalPlan(stored: StoredPlanRequest | null, cachedPlanSet: PlanSet | undefined): DeckPlan | null {
  if (stored && stored.result.proposals.length > 0) {
    const preferred =
      stored.result.proposals.find((proposal) => proposal.strategy === DEFAULT_STRATEGY) ?? stored.result.proposals[0]
    if (preferred?.deckPlan) return preferred.deckPlan
  }
  const fromCache = cachedPlanSet?.plans?.[0]?.deck_plan
  if (fromCache && Array.isArray(fromCache.slides)) return fromCache as DeckPlan
  return null
}

export function StylePage() {
  const { projectId = '' } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const execution = usePlanningExecution()

  const [storedOverride, setStoredOverride] = useState<{ projectId: string; value: StoredPlanRequest | null } | null>(null)
  const freshStored = useMemo(() => readPlanRequest(projectId), [projectId])
  const stored = storedOverride?.projectId === projectId ? storedOverride.value : freshStored
  const [autoPlanning, setAutoPlanning] = useState(false)
  const autoPlanAttemptedRef = useRef<string | null>(null)
  const previewStartedForPlanRef = useRef<string | null>(null)

  const sessionQuery = useAssistantSession(projectId)
  const session = sessionQuery.data
  const { data: project } = useProject(projectId)

  const planIdFromQuery = searchParams.get('planId')
  const planId = planIdFromQuery ?? stored?.result.planId ?? session?.artifacts.plan_id ?? null

  const cachedPlanSet = planId ? queryClient.getQueryData<PlanSet>(assistantKeys.plan(planId)) : undefined
  const basePlan = useMemo(() => selectProposalPlan(stored, cachedPlanSet), [stored, cachedPlanSet])
  const { plan: activePlan, draftKey } = useMemo(() => readEditedPlan(projectId, basePlan), [projectId, basePlan])

  const templateId = stored?.request.template_id ?? session?.artifacts.template_id ?? project?.template_id ?? null
  const contentPackId = stored?.request.content_pack_id ?? session?.artifacts.content_pack_id ?? project?.content_pack_id ?? null
  const dna = useDesignDna(templateId)
  const slideAspectRatio = dna.data?.slide_size.aspect_ratio ?? null

  useEffect(() => {
    if (stored || (!planId && (!templateId || !contentPackId))) return
    if (sessionQuery.isPending || !execution.ready) return
    const key = `${projectId}:${planId ?? ''}:${templateId ?? ''}:${contentPackId ?? ''}`
    if (autoPlanAttemptedRef.current === key) return
    autoPlanAttemptedRef.current = key
    setAutoPlanning(true)
    const rawBrief = session?.brief ?? {}
    const purpose = typeof rawBrief.purpose === 'string' && rawBrief.purpose.trim() ? rawBrief.purpose : 'Итоги проекта'
    const audience = typeof rawBrief.audience === 'string' ? rawBrief.audience : ''
    const language = typeof rawBrief.language === 'string' && rawBrief.language.trim() ? rawBrief.language : 'ru'
    const targetSlideCount = typeof rawBrief.target_slide_count === 'number' ? rawBrief.target_slide_count : (project?.target_slide_count ?? 8)
    const fallbackRequest = {
      template_id: templateId ?? '',
      content_pack_id: contentPackId ?? '',
      strategies: [DEFAULT_STRATEGY],
      brief: {
        purpose,
        audience,
        language,
        target_slide_count: targetSlideCount,
      },
      use_llm: execution.use_llm,
      ...(execution.provider_session_id ? { provider_session_id: execution.provider_session_id } : {}),
    }
    const loadPlan = async () => {
      if (planId) {
        const fetched = await fetchAndStorePlan(projectId, planId, fallbackRequest)
        if (fetched) return fetched
      }
      if (!templateId || !contentPackId) return null
      return requestAndStorePlan(projectId, {
        ...fallbackRequest,
        template_id: templateId,
        content_pack_id: contentPackId,
      })
    }
    void loadPlan()
      .then((nextStored) => {
        if (nextStored) setStoredOverride({ projectId, value: nextStored })
      })
      .catch(() => undefined)
      .finally(() => {
        setAutoPlanning(false)
      })
  }, [stored, planId, templateId, contentPackId, sessionQuery.isPending, projectId, session?.brief, project?.target_slide_count, execution.ready, execution.use_llm, execution.provider_session_id])

  const startPreviews = useStartStylePreviews(planId ?? '')
  const { mutate: triggerStartPreviews } = startPreviews

  const acceptedPreview = startPreviews.data
  const sessionPreviewId = session?.artifacts.preview_id ?? null
  const previewId = acceptedPreview?.preview_id ?? sessionPreviewId
  const jobId = acceptedPreview?.job_id ?? null

  const jobEvents = useJobEvents(jobId)
  const isStreaming = Boolean(jobId) && !jobEvents.done && !jobEvents.error

  const stylePreviewQuery = useStylePreview(previewId)
  const stylePreview = stylePreviewQuery.data
  const sessionPreviewPending = Boolean(sessionPreviewId) && !acceptedPreview && stylePreviewQuery.isPending
  const sessionPreviewFits = Boolean(sessionPreviewId) && !acceptedPreview && stylePreview?.plan_id === planId

  useEffect(() => {
    if (!planId || sessionQuery.isPending || sessionPreviewPending) return
    if (previewStartedForPlanRef.current === planId) return
    previewStartedForPlanRef.current = planId
    if (sessionPreviewFits) return
    triggerStartPreviews({})
  }, [planId, sessionQuery.isPending, sessionPreviewPending, sessionPreviewFits, triggerStartPreviews])

  const unsupported =
    startPreviews.isSuccess &&
    acceptedPreview === null &&
    !session?.artifacts.preview_id &&
    !stylePreview

  const hasPlan = Boolean(planId || activePlan || autoPlanning || (templateId && contentPackId))

  const columns = useMemo(
    () =>
      buildStyleColumns({
        preview: stylePreview,
        events: jobEvents.events,
        isStreaming,
        plan: activePlan,
        unsupported,
      }),
    [stylePreview, jobEvents.events, isStreaming, activePlan, unsupported],
  )

  const recommendedStrategy = useMemo(() => resolveRecommendedStrategy(columns), [columns])
  const [userPickedStrategy, setUserPickedStrategy] = useState<CatalogStrategy | null>(null)
  const selectedStrategy: CatalogStrategy = userPickedStrategy ?? recommendedStrategy
  const [buildExecutionMode, setBuildExecutionMode] = useState<BuildExecutionMode>('checked')
  const [compareSlideId, setCompareSlideId] = useState<string | null>(null)
  const [buildingMode, setBuildingMode] = useState<'single' | 'all' | null>(null)

  const autopilot = useStartAutopilot(projectId)

  const assistantNote = resolveStyleAssistantNote({
    preview: stylePreview,
    events: jobEvents.events,
    sessionNote: session?.assistant_note,
    sessionStep: session?.step,
    isStreaming: isStreaming || startPreviews.isPending || autoPlanning,
    unsupported,
    hasPlan,
    plan: activePlan,
  })

  const handleBuild = (mode: 'single' | 'all') => {
    const strategies: readonly CatalogStrategy[] = mode === 'all' ? STRATEGY_ORDER : [selectedStrategy]
    const payload = buildGenerationPayload({
      stored,
      plan: activePlan,
      session,
      project,
      previewId,
      strategies,
      executionMode: buildExecutionMode,
      modelAvailable: execution.use_llm,
      providerSessionId: execution.provider_session_id,
    })
    if (!payload) return
    setBuildingMode(mode)
    const trackingId = startGeneration(projectId, payload)
    whenGenerationAccepted(trackingId)?.then(
      (accepted) => {
        rememberRun(projectId, accepted.generation_id)
        if (draftKey) clearPlanDraft(draftKey)
      },
      () => undefined,
    )
    navigate(routes.run(projectId, trackingId), {
      state: {
        fromStyle: true,
        strategies: [...strategies],
        deckPlan: payload.deck_plan ?? activePlan ?? null,
        previewId,
        executionMode: buildExecutionMode,
      },
    })
  }

  const handleAutopilot = async () => {
    try {
      const accepted = await autopilot.mutateAsync({
        from_step: 5,
        use_llm: buildExecutionMode === 'fast' ? false : (execution.use_llm || Boolean(stored?.request.use_llm)),
        ...(stored?.request.provider_session_id ? { provider_session_id: stored.request.provider_session_id } : {}),
      })
      if (accepted?.job_id) {
        navigate(routes.run(projectId, accepted.job_id), {
          state: {
            fromStyle: true,
            fromAutopilot: true,
            deckPlan: activePlan ?? null,
            previewId,
            executionMode: buildExecutionMode,
          },
        })
      } else {
        handleBuild('single')
      }
    } catch {
      return
    }
  }

  if (!hasPlan && !sessionQuery.isPending) {
    return (
      <StepShell
        title="Как оформить?"
        note="Сначала согласуйте план — потом примерю стили."
        canvasLabel="Пробные слайды"
        canvas={null}
        actions={
          <Button variant="primary" size="lg" onClick={() => navigate(routes.planDraft(projectId))}>
            К плану
          </Button>
        }
      />
    )
  }

  const canBuild = Boolean(
    buildGenerationPayload({
      stored,
      plan: activePlan,
      session,
      project,
      previewId,
      strategies: [selectedStrategy],
      executionMode: buildExecutionMode,
      modelAvailable: execution.use_llm,
      providerSessionId: execution.provider_session_id,
    }),
  )
  const isSubmitting = buildingMode !== null || autopilot.isPending
  return (
    <StepShell
      title="Как оформить?"
      note={assistantNote}
      canvasLabel="Пробные слайды"
      canvas={
        <div className={styles.table}>
          <div className={styles.grid} role="radiogroup" aria-label="Стиль вёрстки">
            {columns.map((column) => {
              const checked = column.strategy === selectedStrategy
              const radioAlias = column.strategy === 'visual' ? ' (Визуальный)' : ''
              return (
                <ChoiceCard
                  key={column.strategy}
                  as="section"
                  radioName="style"
                  className={styles.column}
                  descriptionClassName={styles.difference}
                  title={column.title}
                  description={column.difference}
                  checked={checked}
                  recommended={column.recommended}
                  badge={
                    column.recommended ? (
                      <Badge tone={checked ? 'ok' : 'ink'} shape="tag">
                        советую
                      </Badge>
                    ) : null
                  }
                  ariaLabel={`${column.title}${radioAlias}${column.recommended ? ', советую' : ''}. ${column.difference}`}
                  data-strategy={column.strategy}
                  onSelect={() => setUserPickedStrategy(column.strategy)}
                >
                  <div className={styles.slides}>
                    {column.slots.map((slot) => (
                      <div key={slot.slideId} className={styles.slide}>
                        <div className={styles.slideMeta}>
                          <span>Слайд {formatIndex(slot.slideNumber)}</span>
                          {slot.purposeLabel && <span className={styles.purpose}>{slot.purposeLabel}</span>}
                        </div>
                        <button
                          type="button"
                          className={styles.slideButton}
                          style={slideAspectRatio ? { aspectRatio: String(slideAspectRatio) } : undefined}
                          aria-label={
                            column.strategy === 'visual'
                              ? `Сравнить слайд ${slot.slideNumber} в трёх стилях (${column.title}) · Сравнить слайд ${slot.slideNumber} в трёх стилях (Визуальный)`
                              : `Сравнить слайд ${slot.slideNumber} в трёх стилях (${column.title})`
                          }
                          onClick={(event) => {
                            event.stopPropagation()
                            setCompareSlideId(slot.slideId)
                          }}
                        >
                          {slot.state === 'ready' && slot.imageUrl ? (
                            <img className={styles.slideImage} src={slot.imageUrl} alt="" />
                          ) : slot.state === 'loading' ? (
                            <span className={styles.skeleton} role="status" aria-label={slot.loadingCaption}>
                              <Skeleton className={styles.skeletonTitle} />
                              <Skeleton className={styles.skeletonLine} />
                              <Skeleton className={styles.skeletonLineShort} />
                            </span>
                          ) : (
                            <span className={styles.unavailable}>Превью недоступно</span>
                          )}
                        </button>
                      </div>
                    ))}
                  </div>
                </ChoiceCard>
              )
            })}
          </div>
        </div>
      }
      actions={
        <div className={styles.actions}>
          <Button
            variant="primary"
            size="lg"
            pending={buildingMode === 'all'}
            disabled={!canBuild || isSubmitting}
            onClick={() => handleBuild('all')}
          >
            Собрать все три
          </Button>
          <Button
            variant="secondary"
            size="lg"
            pending={buildingMode === 'single'}
            disabled={!canBuild || isSubmitting}
            onClick={() => handleBuild('single')}
          >
            Собрать только выбранный
          </Button>
          <Button
            variant="ghost"
            size="lg"
            pending={autopilot.isPending}
            disabled={!canBuild || isSubmitting}
            onClick={() => void handleAutopilot()}
          >
            Сделай сам
          </Button>
        </div>
      }
    >
      <div className={styles.leftPanel}>
        <div className={styles.modeSection}>
          <span className={styles.sectionLabel}>Режим сборки</span>
          <div className={styles.modeSwitch} role="radiogroup" aria-label="Режим сборки">
            {BUILD_MODE_OPTIONS.map((option) => {
              const active = option.mode === buildExecutionMode
              return (
                <ChoiceCard
                  key={option.mode}
                  title={option.label}
                  description={option.summary}
                  ariaLabel={option.label}
                  checked={active}
                  onSelect={() => setBuildExecutionMode(option.mode)}
                />
              )
            })}
          </div>
        </div>

        {startPreviews.isError && (
          <p className={styles.error} role="alert">
            Не получилось показать пробные слайды. Стиль всё равно можно выбрать и собрать.
          </p>
        )}
      </div>
      <StyleCompareDialog
        open={compareSlideId !== null}
        slideId={compareSlideId}
        columns={columns}
        selectedStrategy={selectedStrategy}
        onSelectStrategy={setUserPickedStrategy}
        onSelectSlide={setCompareSlideId}
        onOpenChange={(open) => {
          if (!open) setCompareSlideId(null)
        }}
        onBuildSelected={() => handleBuild('single')}
        buildDisabled={!canBuild || isSubmitting}
        aspectRatio={slideAspectRatio}
      />
    </StepShell>
  )
}
