import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type ReactNode } from 'react'
import { assistantKeys, type PlanSet, usePatchPlanSlide, usePlanSlideAction } from '@/entities/assistant'
import { hasChartData, slidePurposeLabel, type DeckPlan, type GenerationCreate, type SlidePlan } from '@/entities/generation'
import { AgentChat } from '@/features/agent-chat'
import { AssistantCommandBar } from '@/features/assistant-command'
import { applyQuickAction, changeSlideChart, usePlanDraft, useResearchPlanSlide, type QuickSlideAction } from '@/features/edit-deck-plan'
import { humanError, requestOptionalJson } from '@/shared/api'
import { pluralize } from '@/shared/lib/format'
import { Button, LinkButton, Segmented } from '@/shared/ui'
import { PlanCard, resolveDeckKeyMessage } from '@/widgets/deck-plan'
import { StepShell } from '@/widgets/step-shell'
import styles from './PlanPage.module.css'
import { SlideEditor, type EditorSlidePurpose, type SlideEditorPatch } from './SlideEditor'

interface PlanStepProps {
  draftKey: string
  basePlan: DeckPlan
  buildBody: Omit<GenerationCreate, 'deck_plan' | 'deck_plan_id'>
  planId: string | null
  strategy: string | null
  keyMessage: string | null
  assistantNote: string | null
  notice: string | null
  extra?: ReactNode
  onSelectStyle: (plan: DeckPlan) => void
  onAutopilot: (plan: DeckPlan) => void
  autopilotPending: boolean
  onServerPlanSet: (planSet: unknown, optimisticPlan: DeckPlan) => void
  onKeyMessageCommit: (keyMessage: string, nextPlan: DeckPlan) => void
}

type PlanView = 'list' | 'grid'

const VIEW_OPTIONS: readonly { value: PlanView; label: string }[] = [
  { value: 'list', label: 'Список' },
  { value: 'grid', label: 'Сетка' },
]

const DRAFT_STORAGE_PREFIX = 'deckdna.plan-draft.v1:'

const EDITOR_ROLE_LABEL: Record<EditorSlidePurpose, string> = {
  title: 'Титул',
  summary: 'Вывод',
  data: 'Данные',
  comparison: 'Сравнение',
  roadmap: 'План',
  closing: 'Финал',
}

function isUserAddedSlide(slide: SlidePlan): boolean {
  if (slide.purpose !== 'custom') return false
  if (slide.id.startsWith('slide-user-')) return true
  if (slide.evidence_ids.some((raw) => raw.trim().length > 0)) return false
  if (slide.content_units.some((unit) => unit.origin === 'user')) return true
  return !/^slide-\d+$/i.test(slide.id)
}

function toCardSlide(slide: SlidePlan): SlidePlan {
  const purpose: string = slide.purpose
  if (purpose === 'title') return slide
  if (purpose === 'closing' || purpose === 'thank_you') {
    return purpose === 'thank_you' ? slide : { ...slide, purpose: 'thank_you' }
  }
  let roleLabel: string
  if (purpose === 'custom') {
    roleLabel = isUserAddedSlide(slide) ? 'Свой слайд' : 'Контент'
  } else if (purpose in EDITOR_ROLE_LABEL) {
    roleLabel = EDITOR_ROLE_LABEL[purpose as EditorSlidePurpose]
  } else {
    roleLabel = slidePurposeLabel(purpose) ?? 'Контент'
  }
  return purpose === roleLabel ? slide : { ...slide, purpose: roleLabel as SlidePlan['purpose'] }
}

function persistDraftToStorage(draftKey: string, baseId: string, plan: DeckPlan) {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(`${DRAFT_STORAGE_PREFIX}${draftKey}`, JSON.stringify({ baseId, plan }))
  } catch {
    return
  }
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable
}

function planNote(plan: DeckPlan, edited: boolean, explicit: string | null): string {
  if (explicit && explicit.trim()) return explicit.trim()
  const count = plan.slides.length
  const base = `План на ${count} ${pluralize(count, ['слайд', 'слайда', 'слайдов'])}.`
  if (plan.slides.some((slide) => slide.supports_key_message === false)) return `${base} Бледные слайды уводят от главной мысли — проверьте их.`
  if (plan.slides.some((slide) => slide.desired_visual === 'chart' && !hasChartData(slide))) return `${base} Для одного графика не хватает чисел — откройте слайд, я поищу.`
  if (edited) return `${base} Ваши правки сохраню при вёрстке.`
  return `${base} Нажмите на слайд, чтобы поправить заголовок, пункты или график.`
}

export function PlanStep(props: PlanStepProps) {
  const { draftKey, basePlan, buildBody, planId, strategy, keyMessage, assistantNote, notice, extra, onSelectStyle, onAutopilot, autopilotPending, onServerPlanSet, onKeyMessageCommit } =
    props
  const queryClient = useQueryClient()
  const projectId = draftKey.replace(/^draft:/, '').split(':')[0] || ''
  const draft = usePlanDraft(draftKey, basePlan)
  const patchSlide = usePatchPlanSlide(planId ?? '')
  const slideAction = usePlanSlideAction(planId ?? '')
  const researchSlide = useResearchPlanSlide(planId ?? '')
  const [error, setError] = useState<string | null>(null)
  const [researchSupported, setResearchSupported] = useState(true)
  const [researching, setResearching] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [view, setView] = useState<PlanView>('list')
  const resolvedKeyMessage = resolveDeckKeyMessage(draft.plan, keyMessage)
  const [message, setMessage] = useState(resolvedKeyMessage)
  const [prevResolved, setPrevResolved] = useState(resolvedKeyMessage)
  if (resolvedKeyMessage !== prevResolved) {
    setPrevResolved(resolvedKeyMessage)
    if (!message.trim() && resolvedKeyMessage) {
      setMessage(resolvedKeyMessage)
    }
  }

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.shiftKey && event.key.toLowerCase() === 'z') {
        if (isEditableTarget(event.target) || !draft.canUndo) return
        event.preventDefault()
        draft.undo()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [draft])

  const ordered = [...draft.plan.slides].sort((a, b) => a.index - b.index)
  const selected = ordered.find((slide) => slide.id === selectedId) ?? null
  const untitled = ordered.some((slide) => slide.title_intent.trim() === '')
  const chartSlideCount = ordered.filter((slide) => hasChartData(slide) || slide.desired_visual === 'chart').length
  const totalPointsCount = ordered.reduce((sum, slide) => sum + slide.content_units.length, 0)

  const updateSlideDraft = (slideId: string, patch: SlideEditorPatch) => {
    const { purpose, ...rest } = patch
    if (purpose === undefined) {
      draft.update(slideId, rest)
      return
    }
    const nextPlan: DeckPlan = {
      ...draft.plan,
      slides: draft.plan.slides.map((item) => (item.id === slideId ? { ...item, ...rest, purpose: purpose as SlidePlan['purpose'] } : item)),
    }
    draft.replace(nextPlan, true)
    persistDraftToStorage(draftKey, basePlan.id, nextPlan)
  }

  const changeChart = (slideId: string, chartType: string) => {
    setError(null)
    const optimistic = changeSlideChart(draft.plan, slideId, chartType)
    draft.changeChart(slideId, chartType)
    if (!planId) return
    patchSlide.mutate(
      { slideId, chart_type: chartType, ...(strategy ? { strategy } : {}) },
      {
        onSuccess: (planSet) => {
          if (planSet) onServerPlanSet(planSet, optimistic)
        },
        onError: (cause) => {
          draft.undo()
          setError(humanError(cause))
        },
      },
    )
  }

  const quickAction = (slideId: string, action: QuickSlideAction) => {
    setError(null)
    const optimistic = applyQuickAction(draft.plan, slideId, action)
    draft.quickAction(slideId, action)
    if (action === 'remove') setSelectedId(null)
    if (!planId) return
    slideAction.mutate(
      { slideId, action, ...(strategy ? { strategy } : {}) },
      {
        onSuccess: (planSet) => {
          if (planSet) onServerPlanSet(planSet, optimistic)
        },
        onError: (cause) => {
          draft.undo()
          setError(humanError(cause))
        },
      },
    )
  }

  const research = async (slideId: string) => {
    if (!planId || !researchSupported) return
    setError(null)
    setResearching(slideId)
    try {
      const result = await researchSlide.mutateAsync({
        slideId,
        strategy,
        focus: 'numbers',
        useLlm: buildBody.use_llm ?? false,
        providerSessionId: buildBody.provider_session_id ?? null,
      })
      if (!result) {
        setResearchSupported(false)
        return
      }
      if (result.slide) {
        const serverSlide = result.slide
        const currentSlide = draft.plan.slides.find((item) => item.id === slideId)
        const mergedSlide =
          currentSlide?.title_intent.trim() && serverSlide.title_intent !== currentSlide.title_intent
            ? { ...serverSlide, title_intent: currentSlide.title_intent }
            : serverSlide
        const nextPlan: DeckPlan = { ...draft.plan, slides: draft.plan.slides.map((item) => (item.id === slideId ? mergedSlide : item)) }
        draft.replace(nextPlan, true)
        onServerPlanSet(result.plan_set, nextPlan)
      }
    } catch (cause) {
      setError(humanError(cause))
    } finally {
      setResearching(null)
    }
  }

  const commitMessage = () => {
    const next = message.trim()
    if (next === (draft.plan.brief.key_message ?? '').trim()) return
    draft.updateKeyMessage(next)
    onKeyMessageCommit(next, { ...draft.plan, brief: { ...draft.plan.brief, key_message: next } })
  }

  const planWithCurrentMessage = (): DeckPlan => {
    const next = message.trim()
    if (!next || next === (draft.plan.brief.key_message ?? '').trim()) return draft.plan
    const nextPlan: DeckPlan = { ...draft.plan, brief: { ...draft.plan.brief, key_message: next } }
    draft.updateKeyMessage(next)
    persistDraftToStorage(draftKey, basePlan.id, nextPlan)
    return nextPlan
  }

  const busy = autopilotPending
  const disabled = busy || untitled

  return (
    <StepShell
      title="План презентации"
      note={untitled ? 'У каждого слайда должен быть заголовок.' : planNote(draft.plan, draft.edited, assistantNote)}
      canvasLabel="План презентации"
      canvas={
        <div className={styles.canvas} data-view={view}>
          <div className={styles.canvasHead}>
            <span className={styles.canvasCount}>
              {ordered.length} {pluralize(ordered.length, ['слайд', 'слайда', 'слайдов'])}
            </span>
            <Segmented label="Вид плана" size="sm" options={VIEW_OPTIONS} value={view} onChange={setView} />
          </div>
          <ol className={styles.cards} data-view={view} aria-label="Слайды">
            {ordered.map((slide, position) => (
              <li key={slide.id}>
                <PlanCard
                  slide={toCardSlide(slide)}
                  number={position + 1}
                  selected={slide.id === selectedId}
                  offTopic={slide.supports_key_message === false}
                  researching={researching === slide.id}
                  onOpen={() => setSelectedId(slide.id)}
                  onTitleChange={(text) => draft.update(slide.id, { title_intent: text })}
                  onUnitChange={(unit, text) => draft.updateUnit(slide.id, unit, text)}
                  onUnitAdd={() => draft.addUnit(slide.id)}
                  onUnitRemove={(unit) => draft.removeUnit(slide.id, unit)}
                  onChartPick={(type) => changeChart(slide.id, type)}
                />
              </li>
            ))}
            <li>
              <button type="button" className={styles.addSlide} onClick={draft.add}>
                Добавить слайд
              </button>
            </li>
          </ol>
        </div>
      }
      inspector={
        selected
          ? {
              title: `Слайд ${ordered.indexOf(selected) + 1}`,
              onClose: () => setSelectedId(null),
              content: (
                <SlideEditor
                  key={selected.id}
                  plan={draft.plan}
                  slide={selected}
                  first={ordered.indexOf(selected) === 0}
                  last={ordered.indexOf(selected) === ordered.length - 1}
                  researching={researching === selected.id}
                  canResearch={Boolean(planId) && researchSupported}
                  onUpdate={(patch) => updateSlideDraft(selected.id, patch)}
                  onUnitChange={(position, text) => draft.updateUnit(selected.id, position, text)}
                  onUnitAdd={() => draft.addUnit(selected.id)}
                  onUnitRemove={(position) => draft.removeUnit(selected.id, position)}
                  onMove={(delta) => draft.move(selected.id, delta)}
                  onChart={(type) => changeChart(selected.id, type)}
                  onAction={(action) => quickAction(selected.id, action)}
                  onResearch={() => void research(selected.id)}
                  onKeep={() => draft.keepSlideOnTopic(selected.id)}
                />
              ),
            }
          : null
      }
      command={<AgentChat projectId={projectId} planId={planId} slideId={selectedId} slides={ordered}
        onApplied={async () => {
          if (!planId) return
          const result = await queryClient.fetchQuery({ queryKey: assistantKeys.plan(planId), staleTime: 0, queryFn: async () =>
            (await requestOptionalJson<PlanSet>(`/projects/${encodeURIComponent(projectId)}/plans/${encodeURIComponent(planId)}`)) ??
            requestOptionalJson<PlanSet>(`/plans/${encodeURIComponent(planId)}`),
          })
          const next = (result?.plans.find((item) => item.strategy === strategy) ?? result?.plans[0])?.deck_plan
          if (result && next) { draft.replace(next, true); onServerPlanSet(result, next) }
        }}
        fallback={<AssistantCommandBar projectId={projectId} planId={planId} slideId={selectedId} slides={ordered} onApplied={(res) => { const next = res.plan?.plans[0]?.deck_plan; if (res.plan && next) { draft.replace(next, true); onServerPlanSet(res.plan, next) } }} />}
      />}
      actions={
        <>
          <Button variant="primary" size="lg" disabled={disabled} onClick={() => onSelectStyle(planWithCurrentMessage())}>
            Выбрать стиль
          </Button>
          <Button variant="ghost" size="lg" pending={autopilotPending} disabled={disabled} onClick={() => onAutopilot(planWithCurrentMessage())}>
            Сделай сам
          </Button>
        </>
      }
    >
      <dl className={styles.planStats} aria-label="Состав плана">
        <div className={styles.planStat}>
          <dt className={styles.planStatLabel}>{pluralize(ordered.length, ['слайд', 'слайда', 'слайдов'])}</dt>
          <dd className={styles.planStatValue}>{ordered.length}</dd>
        </div>
        <div className={styles.planStat}>
          <dt className={styles.planStatLabel}>{pluralize(chartSlideCount, ['график', 'графика', 'графиков'])}</dt>
          <dd className={styles.planStatValue}>{chartSlideCount}</dd>
        </div>
        <div className={styles.planStat}>
          <dt className={styles.planStatLabel}>{pluralize(totalPointsCount, ['пункт', 'пункта', 'пунктов'])}</dt>
          <dd className={styles.planStatValue}>{totalPointsCount}</dd>
        </div>
      </dl>
      <label className={styles.messageLabel}>
        <span>Главная мысль</span>
        <textarea
          className={styles.message}
          rows={3}
          value={message}
          placeholder="Что слушатели должны запомнить"
          onChange={(event) => setMessage(event.target.value)}
          onBlur={commitMessage}
        />
      </label>
      {extra}
      {(draft.canUndo || draft.edited) && (
        <div className={styles.links}>
          {draft.canUndo && (
            <LinkButton onClick={draft.undo}>
              Отменить последнее
            </LinkButton>
          )}
          {draft.edited && (
            <LinkButton onClick={draft.reset}>
              Вернуть исходный план
            </LinkButton>
          )}
        </div>
      )}
      {(error || notice) && (
        <p className={styles.error} role="alert">
          {error ?? notice}
        </p>
      )}
    </StepShell>
  )
}
