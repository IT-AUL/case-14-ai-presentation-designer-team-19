import { useEffect, useMemo, useState } from 'react'
import { Link, useLocation, useNavigate, useParams } from 'react-router'
import { useAssistantSession, useJobEvents } from '@/entities/assistant'
import { isTrackingId, parseContentFill, useGenerationTracker, useJob, type DeckPlan, type GenerationPhase, type GenerationTracker } from '@/entities/generation'
import { markEvidence, projectRunId, rememberRun, useProject } from '@/entities/project'
import { useTemplate } from '@/entities/template'
import { hasPendingVariantChanges, useVariantChanges } from '@/entities/variant'
import { CancelGenerationButton } from '@/features/cancel-generation'
import { fetchAndStorePlan, readPlanRequest, type StoredPlanRequest } from '@/features/edit-deck-plan'
import { useRerunGeneration } from '@/features/rerun-generation'
import {
  BuildSlideInspector,
  BuildTable,
  BuildVariants,
  describeFailure,
  extractEventGenerationId,
  failureMeta,
  isTrackingLost,
  latestAssistantNote,
  RunNotice,
  useBuildCards,
  useBuildSlots,
  useSelectedBuildKey,
} from '@/widgets/generation-tracker'
import { StepShell } from '@/widgets/step-shell'
import { routes } from '@/shared/config'
import { formatIndex } from '@/shared/lib/format'
import { Button } from '@/shared/ui'
import styles from './GenerationPage.module.css'

const AUTO_POLISH_SECONDS = 3
const AUTO_POLISH_TICK_MS = 900

interface RunLocationState {
  fromStyle?: boolean
  fromAutopilot?: boolean
  fromStepBar?: boolean
  strategies?: string[]
  deckPlan?: DeckPlan | null
  previewId?: string | null
}

function isRunLocationState(value: unknown): value is RunLocationState {
  return typeof value === 'object' && value !== null
}

function lead(phase: GenerationPhase, templateName: string | null): string {
  switch (phase) {
    case 'submitting':
    case 'running':
      return templateName ? `Шаблон «${templateName}». Можно уйти с экрана — сборка продолжится.` : 'Можно уйти с экрана — сборка продолжится.'
    case 'completed':
      return 'Проверьте слайды и доработайте то, что нужно.'
    case 'failed':
      return 'Бриф и файлы сохранены.'
    case 'canceled':
      return 'Бриф и загруженные файлы сохранены.'
  }
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
            <Link className={canOpenLatest ? styles.secondaryLink : styles.primaryLink} to={routes.planDraft(projectId)}>
              К плану
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
      title={phase === 'failed' ? 'Вёрстка не завершилась' : 'Сборка отменена'}
      message={failure ? failure.message : 'Сборку можно запустить заново с теми же данными.'}
      meta={failure ? failureMeta(failure) : null}
      details={failure?.technical ? [failureMeta(failure), failure.technical].filter(Boolean).join('\n') : null}
      actions={
        <>
          {retry}
          <Link className={retry ? styles.secondaryLink : styles.primaryLink} to={routes.planDraft(projectId)}>
            К плану
          </Link>
        </>
      }
    />
  )
}

export function GenerationPage() {
  const { projectId = '', runId = '' } = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const routeState = isRunLocationState(location.state) ? location.state : null

  const isJobRun = runId.startsWith('job_')
  const autopilotJobQuery = useJob(isJobRun ? runId : null)
  const autopilotJob = autopilotJobQuery.data
  const jobIdForEvents = isJobRun ? runId : null
  const preEvents = useJobEvents(jobIdForEvents)

  const resolvedRunIdFromJob =
    (typeof autopilotJob?.result_ids?.generation_id === 'string' ? autopilotJob.result_ids.generation_id : null) ??
    extractEventGenerationId(preEvents.events)

  const trackerTargetId = isJobRun ? resolvedRunIdFromJob : runId
  const tracker = useGenerationTracker(trackerTargetId)

  const activeJobId = isJobRun ? runId : tracker.jobId
  const jobEvents = useJobEvents(activeJobId)

  const phase: GenerationPhase =
    isJobRun && !resolvedRunIdFromJob
      ? autopilotJob?.state === 'failed'
        ? 'failed'
        : autopilotJob?.state === 'canceled'
          ? 'canceled'
          : 'running'
      : tracker.phase

  const generationId = tracker.generationId ?? resolvedRunIdFromJob
  const canCancel = tracker.canCancel
  const tracking = isTrackingId(runId)

  const requestedStrategies = routeState?.strategies

  const lost = !isJobRun && isTrackingLost(tracker.error)
  const { data: project } = useProject(projectId)
  const { data: session } = useAssistantSession(projectId)
  const serverLatestRunId = project?.latest_run_id ?? null
  const { data: template } = useTemplate(tracker.generation?.template_id)

  const initialStoredPlan = useMemo(() => readPlanRequest(projectId), [projectId])
  const [fetchedStoredPlan, setFetchedStoredPlan] = useState<StoredPlanRequest | null>(null)
  const storedPlan = initialStoredPlan ?? fetchedStoredPlan
  const deckPlan: DeckPlan | null =
    tracker.generation?.deck_plan ??
    routeState?.deckPlan ??
    storedPlan?.result.proposals[0]?.deckPlan ??
    null
  const sessionPlanId = session?.artifacts.plan_id ?? tracker.generation?.deck_plan_id ?? null

  useEffect(() => {
    if (deckPlan || !projectId || !sessionPlanId) return
    const controller = new AbortController()
    const templateId = tracker.generation?.template_id ?? session?.artifacts.template_id ?? ''
    const contentPackId = tracker.generation?.content_pack_id ?? session?.artifacts.content_pack_id ?? ''
    void fetchAndStorePlan(
      projectId,
      sessionPlanId,
      {
        template_id: templateId,
        content_pack_id: contentPackId,
        brief: { purpose: 'Презентация', audience: '', language: 'ru', target_slide_count: 12 },
      },
      {},
      { signal: controller.signal },
    )
      .then((loaded) => {
        if (loaded) setFetchedStoredPlan(loaded)
      })
      .catch(() => undefined)
    return () => controller.abort()
  }, [deckPlan, projectId, sessionPlanId, tracker.generation?.template_id, tracker.generation?.content_pack_id, session?.artifacts.template_id, session?.artifacts.content_pack_id])

  useEffect(() => {
    if ((!tracking && !isJobRun) || !generationId) return
    rememberRun(projectId, generationId)
    navigate(routes.run(projectId, generationId), { replace: true, state: location.state })
  }, [tracking, isJobRun, generationId, projectId, navigate, location.state])

  useEffect(() => {
    if (!lost || !serverLatestRunId || serverLatestRunId === runId) return
    rememberRun(projectId, serverLatestRunId)
    navigate(routes.run(projectId, serverLatestRunId), { replace: true, state: location.state })
  }, [lost, serverLatestRunId, runId, projectId, navigate, location.state])

  useEffect(() => {
    if (phase === 'completed') markEvidence(projectId, 'generated')
  }, [phase, projectId])

  const effectiveTracker: GenerationTracker = useMemo(
    () => (isJobRun && !resolvedRunIdFromJob ? { ...tracker, phase, jobId: runId } : tracker),
    [isJobRun, resolvedRunIdFromJob, tracker, phase, runId],
  )

  const cards = useBuildCards(effectiveTracker, requestedStrategies)
  const [selectedKey, setSelectedKey] = useSelectedBuildKey(cards)
  const { slots, stageSummary, variantId: activeVariantId } = useBuildSlots({
    tracker: effectiveTracker,
    events: jobEvents.events,
    deckPlan,
    cards,
    selectedKey,
  })
  const totalDeckSlides = deckPlan?.slides.length || slots.length || undefined
  const contentFill = useMemo(
    () =>
      parseContentFill((tracker.generation as unknown as Record<string, unknown> | undefined)?.content_fill, tracker.generation) ??
      storedPlan?.result.contentFill ??
      null,
    [tracker.generation, storedPlan],
  )
  const assistantNote = useMemo(
    () =>
      latestAssistantNote(jobEvents.events, activeVariantId, totalDeckSlides) ??
      latestAssistantNote(jobEvents.events, null, totalDeckSlides),
    [jobEvents.events, activeVariantId, totalDeckSlides],
  )
  const changesQuery = useVariantChanges(activeVariantId)
  const hasPendingChanges = hasPendingVariantChanges(changesQuery.data)

  const [inspectedSlideIndex, setInspectedSlideIndex] = useState<number | null>(null)
  const [autoPolishCanceled, setAutoPolishCanceled] = useState(false)
  const [autoPolishTicks, setAutoPolishTicks] = useState(0)

  const inspectedSlot = inspectedSlideIndex !== null ? (slots[inspectedSlideIndex] ?? null) : null
  const buildingSlot = slots.find((slot) => slot.state === 'building' && !slot.imageUrl) ?? null
  const done = phase === 'completed' && Boolean(generationId)
  const deckShownOnTable = done && slots.length > 0 && slots.some((slot) => slot.state === 'ready')

  const shouldAutoPolish = Boolean(
    !routeState?.fromStepBar &&
      (routeState?.fromStyle ||
        routeState?.fromAutopilot ||
        isJobRun ||
        jobEvents.done ||
        jobEvents.events.some((event) => event.type === 'slide.ready' || event.type === 'stage.started') ||
        tracker.generation?.variants.length === 1),
  )

  const autoPolishActive = Boolean(
    deckShownOnTable &&
      generationId &&
      shouldAutoPolish &&
      !autoPolishCanceled &&
      !hasPendingChanges &&
      inspectedSlot === null,
  )

  const autoPolishSecondsLeft = autoPolishActive ? Math.max(0, AUTO_POLISH_SECONDS - autoPolishTicks) : null

  useEffect(() => {
    if (!autoPolishActive || !generationId) return
    let ticks = 0
    const interval = setInterval(() => {
      ticks += 1
      setAutoPolishTicks(ticks)
      if (ticks >= AUTO_POLISH_SECONDS) {
        clearInterval(interval)
        navigate(routes.polish(projectId, generationId))
      }
    }, AUTO_POLISH_TICK_MS)
    return () => clearInterval(interval)
  }, [autoPolishActive, generationId, navigate, projectId])

  const handleSelectStyle = (key: string) => {
    setSelectedKey(key)
    setInspectedSlideIndex(null)
  }

  const handleSelectSlide = (index: number) => {
    setAutoPolishCanceled(true)
    setInspectedSlideIndex((prev) => (prev === index ? null : index))
  }

  const title = lost ? 'Сборка потерялась' : phase === 'completed' ? 'Презентация готова' : phase === 'failed' ? 'Не получилось собрать' : 'Собираю презентацию'
  const note = lost
    ? 'Материалы и план сохранены.'
    : phase === 'completed' || phase === 'failed' || phase === 'canceled'
      ? lead(phase, null)
      : (assistantNote ?? lead(phase, template?.filename ?? null))

  return (
    <StepShell
      title={title}
      note={note}
      canvasLabel="Слайды презентации"
      canvas={
        lost ? null : (
          <BuildTable
            tracker={effectiveTracker}
            events={jobEvents.events}
            deckPlan={deckPlan}
            cards={cards}
            selectedKey={selectedKey}
            slots={slots}
            selectedSlideIndex={inspectedSlideIndex}
            onSelectSlide={handleSelectSlide}
            onSlideChangeRequested={() => setAutoPolishCanceled(true)}
          />
        )
      }
      inspector={
        inspectedSlot
          ? {
              title: `${formatIndex(inspectedSlot.slideNumber)} · ${inspectedSlot.title}`,
              wide: true,
              onClose: () => setInspectedSlideIndex(null),
              content: (
                <BuildSlideInspector
                  slot={inspectedSlot}
                  totalSlides={slots.length}
                  onSelectSlide={(nextIdx) => setInspectedSlideIndex(nextIdx)}
                />
              ),
            }
          : null
      }
      actions={
        done && generationId ? (
          <Button variant="primary" size="lg" onClick={() => navigate(routes.polish(projectId, generationId))}>
            Открыть и доработать
          </Button>
        ) : phase === 'running' && canCancel && generationId ? (
          <CancelGenerationButton generationId={generationId} onCanceled={() => navigate(routes.planDraft(projectId))} />
        ) : phase === 'submitting' && !generationId ? (
          <CancelGenerationButton generationId={null} />
        ) : undefined
      }
    >
      <BuildVariants
        cards={cards}
        selectedKey={selectedKey}
        onSelect={handleSelectStyle}
        stageSummary={stageSummary}
        autoPolishSecondsLeft={autoPolishSecondsLeft}
        onCancelAutoPolish={() => setAutoPolishCanceled(true)}
        currentSlide={buildingSlot ? { number: buildingSlot.slideNumber, title: buildingSlot.title } : null}
        contentFill={contentFill}
        actualSlides={totalDeckSlides ?? null}
      />
      <RecoveryNotice projectId={projectId} runId={runId} tracker={effectiveTracker} />
    </StepShell>
  )
}
