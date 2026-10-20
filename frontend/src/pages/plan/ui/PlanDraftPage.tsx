import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import { useAssistantSession, useStartAutopilot } from '@/entities/assistant'
import { contentFillValue, type DeckPlan, type GenerationBrief, type GenerationCreate, type Job, type PlanProposal, type SlidePlan } from '@/entities/generation'
import { rememberRun } from '@/entities/project'
import { DEFAULT_STRATEGY, STRATEGY_ORDER, type CatalogStrategy } from '@/entities/variant'
import {
  applyServerPlanSet,
  fetchAndStorePlan,
  isAbortError,
  PlanBuildProgress,
  readPlanRequest,
  requestAndStorePlan,
  syncPlanDraftToStorage,
  useBuildFromPlan,
  usePlanBuild,
  usePlanningExecution,
  type StoredPlanRequest,
} from '@/features/edit-deck-plan'
import { requestOptionalJson, humanError } from '@/shared/api'
import { API_BASE, routes } from '@/shared/config'
import { Button, PlanBoardSkeleton } from '@/shared/ui'
import { resolveDeckKeyMessage } from '@/widgets/deck-plan'
import { StepShell } from '@/widgets/step-shell'
import { PlanExtras, type PlanAdjustment } from './PlanExtras'
import { PlanStep } from './PlanStep'

const DRAFT_STORAGE_PREFIX = 'deckdna.plan-draft.v1:'
const TECHNICAL_PURPOSES = new Set(['presentation', 'презентация', 'feature', 'product', 'project', 'initiative'])

function isMeaningfulBriefString(value: unknown): value is string {
  if (typeof value !== 'string') return false
  const trimmed = value.trim()
  return Boolean(trimmed) && !TECHNICAL_PURPOSES.has(trimmed.toLowerCase())
}

function isDeckPlanCandidate(value: unknown): value is DeckPlan {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false
  const record = value as Record<string, unknown>
  return typeof record.id === 'string' && Array.isArray(record.slides) && typeof record.brief === 'object' && record.brief !== null
}

function readDraftPlanFromStorage(draftKey: string, basePlan: DeckPlan, candidatePlan?: DeckPlan): DeckPlan {
  const candidateEdited = Boolean(candidatePlan && JSON.stringify(candidatePlan) !== JSON.stringify(basePlan))
  if (typeof window !== 'undefined') {
    if (candidateEdited && candidatePlan) {
      try {
        window.localStorage.setItem(`${DRAFT_STORAGE_PREFIX}${draftKey}`, JSON.stringify({ baseId: basePlan.id, plan: candidatePlan }))
      } catch {
        return candidatePlan
      }
      return candidatePlan
    }
    for (const key of [`${DRAFT_STORAGE_PREFIX}${draftKey}`, draftKey]) {
      try {
        const raw = window.localStorage.getItem(key)
        if (!raw) continue
        const parsed = JSON.parse(raw) as unknown
        if (isDeckPlanCandidate(parsed)) return parsed
        if (typeof parsed === 'object' && parsed !== null && isDeckPlanCandidate((parsed as { plan?: unknown }).plan)) {
          return (parsed as { plan: DeckPlan }).plan
        }
      } catch {
        continue
      }
    }
  }
  return candidatePlan ?? basePlan
}

function slideChanged(original: SlidePlan | undefined, current: SlidePlan): boolean {
  if (!original) return true
  if (original.title_intent !== current.title_intent) return true
  if (original.key_message !== current.key_message) return true
  if (original.purpose !== current.purpose) return true
  if ((original.desired_visual ?? null) !== (current.desired_visual ?? null)) return true
  if (original.index !== current.index) return true
  if (JSON.stringify(original.content_units) !== JSON.stringify(current.content_units)) return true
  if (JSON.stringify(original.chart ?? null) !== JSON.stringify(current.chart ?? null)) return true
  return false
}

function extractChartType(slide: SlidePlan): string | null {
  if (typeof slide.chart === 'object' && slide.chart !== null && !Array.isArray(slide.chart)) {
    const type = (slide.chart as { type?: unknown }).type
    if (typeof type === 'string' && type.trim()) return type.trim()
  }
  return null
}

async function syncPlanSlidesBeforeAutopilot(
  planId: string | null,
  basePlan: DeckPlan,
  draftPlan: DeckPlan,
  strategy: string | null,
): Promise<void> {
  if (!planId) return
  const baseById = new Map(basePlan.slides.map((slide) => [slide.id, slide]))
  const draftIds = new Set(draftPlan.slides.map((slide) => slide.id))

  for (const original of basePlan.slides) {
    if (!draftIds.has(original.id)) {
      try {
        await fetch(`${API_BASE}/plans/${encodeURIComponent(planId)}/slides/${encodeURIComponent(original.id)}/actions`, {
          method: 'POST',
          headers: { 'content-type': 'application/json', accept: 'application/json' },
          body: JSON.stringify({ action: 'remove', ...(strategy ? { strategy } : {}) }),
        })
      } catch {
        continue
      }
    }
  }

  for (const slide of draftPlan.slides) {
    const original = baseById.get(slide.id)
    if (!slideChanged(original, slide)) continue
    const chartType = extractChartType(slide)
    try {
      await fetch(`${API_BASE}/plans/${encodeURIComponent(planId)}/slides/${encodeURIComponent(slide.id)}`, {
        method: 'PATCH',
        headers: { 'content-type': 'application/json', accept: 'application/json' },
        body: JSON.stringify({
          title_intent: slide.title_intent,
          key_message: slide.key_message,
          purpose: slide.purpose,
          desired_visual: slide.desired_visual,
          index: slide.index,
          content_units: slide.content_units,
          ...(chartType ? { chart_type: chartType } : {}),
          ...(strategy ? { strategy } : {}),
        }),
      })
    } catch {
      continue
    }
  }
}

function catalogStrategies(strategies: readonly string[]): CatalogStrategy[] {
  const known = STRATEGY_ORDER.filter((strategy) => strategies.includes(strategy))
  return known.length > 0 ? known : [...STRATEGY_ORDER]
}

function buildBody({ request, variants }: StoredPlanRequest): Omit<GenerationCreate, 'deck_plan' | 'deck_plan_id'> {
  const { strategies, ...body } = request
  return { ...body, variants: variants && variants.length > 0 ? variants : catalogStrategies(strategies ?? []).map((strategy) => ({ strategy })) }
}

function proposalKey(proposal: PlanProposal, index: number): string {
  return proposal.strategy ?? `plan-${index}`
}

function defaultProposal(proposals: readonly PlanProposal[]): string {
  const preferred = proposals.findIndex((proposal) => proposal.strategy === DEFAULT_STRATEGY)
  const index = preferred >= 0 ? preferred : 0
  const proposal = proposals[index]
  return proposal ? proposalKey(proposal, index) : ''
}

function errorText(error: unknown): string | null {
  if (!error || isAbortError(error)) return null
  return humanError(error, 'Не получилось составить план. Проверьте материалы и попробуйте ещё раз.')
}

function adjustedRequest(stored: StoredPlanRequest, adjustment: PlanAdjustment, excluded: readonly string[]): StoredPlanRequest['request'] {
  const { brief } = stored.request
  const contentFill = adjustment.contentFill ?? stored.contentFill ?? null
  const nextBrief = {
    ...brief,
    target_slide_count: adjustment.slideCount ?? brief.target_slide_count,
    ...(contentFill ? { content_fill: contentFillValue(contentFill) } : {}),
  }
  return { ...stored.request, brief: nextBrief, ...(excluded.length > 0 ? { exclude_source_ids: [...excluded] } : {}) }
}

function excludedSources(stored: StoredPlanRequest): string[] {
  const value = (stored.request as { exclude_source_ids?: unknown }).exclude_source_ids
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function sessionBriefToGenerationBrief(raw: Record<string, unknown>): GenerationBrief {
  const purpose = isMeaningfulBriefString(raw.purpose) ? raw.purpose.trim() : 'Презентация'
  const keyMessage = isMeaningfulBriefString(raw.key_message)
    ? raw.key_message.trim()
    : isMeaningfulBriefString(raw.purpose)
      ? raw.purpose.trim()
      : undefined
  return {
    purpose,
    audience: typeof raw.audience === 'string' ? raw.audience : '',
    language: typeof raw.language === 'string' && raw.language ? raw.language : 'ru',
    target_slide_count: typeof raw.target_slide_count === 'number' && Number.isFinite(raw.target_slide_count) ? raw.target_slide_count : 8,
    ...(typeof raw.tone === 'string' ? { tone: raw.tone } : {}),
    ...(typeof raw.content_fill === 'string' ? { content_fill: raw.content_fill as GenerationBrief['content_fill'] } : {}),
    ...(keyMessage ? { key_message: keyMessage } : {}),
    ...(typeof raw.call_to_action === 'string' ? { call_to_action: raw.call_to_action } : {}),
  }
}

const CANONICAL_BRIEF_FIELDS = ['purpose', 'audience', 'language', 'key_message', 'call_to_action'] as const

function withCanonicalBrief(plan: DeckPlan, sessionBrief: Record<string, unknown> | undefined): DeckPlan {
  const brief = { ...plan.brief }
  for (const field of CANONICAL_BRIEF_FIELDS) {
    const value = sessionBrief?.[field]
    if (typeof value !== 'string' || !value.trim()) continue
    if ((field === 'purpose' || field === 'key_message') && !isMeaningfulBriefString(value)) continue
    if (field === 'key_message' && isMeaningfulBriefString(brief.key_message)) continue
    Object.assign(brief, { [field]: value.trim() })
  }
  const resolvedKey = resolveDeckKeyMessage({ ...plan, brief }, brief.key_message ?? null, sessionBrief ?? null)
  if (resolvedKey && !isMeaningfulBriefString(brief.key_message)) {
    brief.key_message = resolvedKey
  }
  const slides = plan.slides.map((slide) => {
    if (slide.purpose === 'title' && /\.(csv|tsv|xlsx|xls)$/i.test(slide.title_intent.trim())) {
      const cleanCover =
        isMeaningfulBriefString(brief.purpose) && !/\.(csv|tsv|xlsx|xls)$/i.test(brief.purpose)
          ? brief.purpose.trim()
          : resolvedKey && !/\.(csv|tsv|xlsx|xls)$/i.test(resolvedKey)
            ? resolvedKey.trim()
            : slide.title_intent
      return cleanCover !== slide.title_intent ? { ...slide, title_intent: cleanCover } : slide
    }
    return slide
  })
  return { ...plan, brief, slides }
}

export function PlanDraftPage() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const [stored, setStored] = useState(() => readPlanRequest(projectId))
  const [rebuildError, setRebuildError] = useState<string | null>(null)
  const [rebuilding, setRebuilding] = useState(false)
  const [autopilotNote, setAutopilotNote] = useState<string | null>(null)
  const autoBuildAttempted = useRef(false)
  const planBuild = usePlanBuild()
  const execution = usePlanningExecution()
  const sessionQuery = useAssistantSession(projectId)
  const autopilot = useStartAutopilot(projectId)
  const proposals = useMemo(() => stored?.result.proposals ?? [], [stored])
  const [selected, setSelected] = useState(() => defaultProposal(proposals))

  const index = proposals.findIndex((proposal, position) => proposalKey(proposal, position) === selected)
  const proposal = proposals[index] ?? proposals[0]
  const draftKey = `draft:${projectId}:${proposal?.deckPlan.id ?? 'empty'}`
  const fallbackBuild = useBuildFromPlan(projectId, draftKey)

  const sessionPlanId = sessionQuery.data?.artifacts.plan_id ?? null
  const sessionTemplateId = sessionQuery.data?.artifacts.template_id ?? null
  const sessionContentPackId = sessionQuery.data?.artifacts.content_pack_id ?? null
  const canAutoBuild = Boolean(!stored && (sessionPlanId || (sessionTemplateId && sessionContentPackId)))

  useEffect(() => {
    if (stored || rebuilding || autoBuildAttempted.current || !execution.ready) return
    const session = sessionQuery.data
    const templateId = session?.artifacts.template_id
    const contentPackId = session?.artifacts.content_pack_id
    const existingPlanId = session?.artifacts.plan_id ?? null
    if (!existingPlanId && (!templateId || !contentPackId)) return
    autoBuildAttempted.current = true
    const brief = sessionBriefToGenerationBrief(session?.brief ?? {})
    const fallbackRequest = {
      template_id: templateId ?? '',
      content_pack_id: contentPackId ?? '',
      brief,
      strategies: [DEFAULT_STRATEGY],
      use_llm: execution.use_llm,
      ...(execution.provider_session_id ? { provider_session_id: execution.provider_session_id } : {}),
    }
    queueMicrotask(() => {
      const options = planBuild.begin()
      planBuild.enterPhase('plan')
      setRebuilding(true)
      setRebuildError(null)
      const loadPlan = async () => {
        if (existingPlanId) {
          const fetched = await fetchAndStorePlan(
            projectId,
            existingPlanId,
            fallbackRequest,
            { keyMessage: brief.key_message ?? null },
            { signal: options.signal },
          )
          const fetchedPackId = (fetched?.result.proposals[0]?.deckPlan as { content_pack_id?: string | null } | undefined)?.content_pack_id
          if (fetched && (!contentPackId || !fetchedPackId || fetchedPackId === contentPackId)) {
            return fetched
          }
        }
        if (!templateId || !contentPackId) {
          throw new Error('План не найден')
        }
        return requestAndStorePlan(
          projectId,
          {
            ...fallbackRequest,
            template_id: templateId,
            content_pack_id: contentPackId,
          },
          { keyMessage: brief.key_message ?? null },
          options,
        )
      }
      void loadPlan()
        .then((next) => {
          setStored(next)
          setSelected(defaultProposal(next.result.proposals))
        })
        .catch((error) => {
          setRebuildError(errorText(error))
        })
        .finally(() => {
          planBuild.finish()
          setRebuilding(false)
        })
    })
  }, [stored, rebuilding, sessionQuery.data, projectId, planBuild, execution.ready, execution.use_llm, execution.provider_session_id])

  if (rebuilding) {
    return (
      <StepShell title="Составляю план" note="Собираю структуру из ваших материалов…" canvasLabel="План презентации" canvas={<PlanBoardSkeleton />}>
        <PlanBuildProgress progress={planBuild.progress} phase={planBuild.phase} startedAt={planBuild.startedAt} onCancel={planBuild.cancel} />
      </StepShell>
    )
  }

  if (!stored || !proposal) {
    const waiting = sessionQuery.isLoading || (canAutoBuild && !rebuildError)
    return (
      <StepShell
        title="План презентации"
        note={waiting ? 'Загружаю план…' : (rebuildError ?? 'Плана пока нет. Вернитесь к материалам и нажмите «Дальше».')}
        canvasLabel="План презентации"
        canvas={waiting ? <PlanBoardSkeleton /> : null}
        actions={
          waiting ? undefined : (
            <Button variant="primary" size="lg" onClick={() => navigate(routes.start(projectId))}>
              К материалам
            </Button>
          )
        }
      />
    )
  }

  const rebuild = async (adjustment: PlanAdjustment, excluded = excludedSources(stored)) => {
    const options = planBuild.begin()
    planBuild.enterPhase('plan')
    setRebuilding(true)
    setRebuildError(null)
    try {
      const contentFill = adjustment.contentFill ?? stored.contentFill ?? null
      const next = await requestAndStorePlan(projectId, adjustedRequest(stored, adjustment, excluded), { variants: stored.variants, contentFill }, options)
      setStored(next)
      setSelected(defaultProposal(next.result.proposals))
    } catch (error) {
      setRebuildError(errorText(error))
    } finally {
      planBuild.finish()
      setRebuilding(false)
    }
  }

  const handleSelectStyle = (draftPlan: DeckPlan) => {
    const syncedPlan = withCanonicalBrief(readDraftPlanFromStorage(draftKey, proposal.deckPlan, draftPlan), sessionQuery.data?.brief)
    const next = syncPlanDraftToStorage(projectId, stored, syncedPlan, proposal.strategy)
    setStored(next)
    void syncPlanSlidesBeforeAutopilot(planId, proposal.deckPlan, syncedPlan, proposal.strategy)
    navigate(routes.style(projectId))
  }

  const handleAutopilot = async (draftPlan: DeckPlan) => {
    const syncedPlan = withCanonicalBrief(readDraftPlanFromStorage(draftKey, proposal.deckPlan, draftPlan), sessionQuery.data?.brief)
    const next = syncPlanDraftToStorage(projectId, stored, syncedPlan, proposal.strategy)
    setStored(next)
    setRebuildError(null)
    try {
      await syncPlanSlidesBeforeAutopilot(planId, proposal.deckPlan, syncedPlan, proposal.strategy)
      const accepted = await autopilot.mutateAsync({
        from_step: 4,
        use_llm: Boolean(next.request.use_llm),
        ...(next.request.provider_session_id ? { provider_session_id: next.request.provider_session_id } : {}),
      })
      if (!accepted) {
        fallbackBuild.build(buildBody(next), syncedPlan, { lockAll: next.contentFill === 'keep' })
        return
      }
      if (accepted.assistant_note) setAutopilotNote(accepted.assistant_note)
      const job = await requestOptionalJson<Job>(`/jobs/${encodeURIComponent(accepted.job_id)}`)
      const resultIds = job?.result_ids && typeof job.result_ids === 'object' ? (job.result_ids as Record<string, unknown>) : null
      const generationId = typeof resultIds?.generation_id === 'string' && resultIds.generation_id ? resultIds.generation_id : null
      const targetRunId = generationId ?? accepted.job_id
      if (generationId) rememberRun(projectId, generationId)
      navigate(routes.run(projectId, targetRunId))
    } catch (error) {
      setRebuildError(errorText(error))
    }
  }

  const { sufficiency, research, contentFill, planId } = stored.result
  const canonicalBasePlan = withCanonicalBrief(proposal.deckPlan, sessionQuery.data?.brief)
  const resolvedKeyMessage = resolveDeckKeyMessage(canonicalBasePlan, stored.keyMessage, sessionQuery.data?.brief ?? null)

  return (
    <PlanStep
      key={canonicalBasePlan.id}
      draftKey={draftKey}
      basePlan={canonicalBasePlan}
      buildBody={buildBody(stored)}
      planId={planId}
      strategy={proposal.strategy}
      keyMessage={resolvedKeyMessage || null}
      assistantNote={autopilotNote ?? stored.assistantNote ?? null}
      notice={rebuildError ? `Не удалось перестроить план: ${rebuildError}` : null}
      onSelectStyle={handleSelectStyle}
      onAutopilot={(plan) => void handleAutopilot(plan)}
      autopilotPending={autopilot.isPending || fallbackBuild.isPending}
      onServerPlanSet={(planSet, optimisticPlan) => {
        setStored((current) => {
          if (!current) return current
          const applied = applyServerPlanSet(projectId, current, planSet)
          return optimisticPlan ? syncPlanDraftToStorage(projectId, applied, optimisticPlan, proposal.strategy) : applied
        })
      }}
      onKeyMessageCommit={(_, nextPlan) => {
        setStored((current) => (current ? syncPlanDraftToStorage(projectId, current, nextPlan, proposal.strategy) : current))
      }}
      extra={
        <PlanExtras
          sufficiency={sufficiency}
          contentFill={contentFill ?? null}
          sources={research?.sources ?? []}
          targetSlides={stored.request.brief.target_slide_count}
          acceptedShortage={stored.contentFill === 'keep'}
          disabled={rebuilding}
          onAdjust={(adjustment) => void rebuild(adjustment)}
          onRemoveSource={(sourceId) => void rebuild({}, [...excludedSources(stored), sourceId])}
          onAddFiles={() => navigate(routes.start(projectId))}
          onAnswerQuestions={() => navigate(routes.questions(projectId))}
        />
      }
    />
  )
}
