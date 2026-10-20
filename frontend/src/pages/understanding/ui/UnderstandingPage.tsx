import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import {
  assistantKeys,
  useAddNotes,
  useAssistantSession,
  useEditBlocks,
  useEditInsight,
  useQuestions,
  useStartAutopilot,
  useSubmitAnswers,
  useUnderstanding,
  type BlockOps,
  type InsightPatch,
  type Understanding,
} from '@/entities/assistant'
import { useContentPack } from '@/entities/content-pack'
import { parseContentFill } from '@/entities/generation'
import { requestJson, humanError } from '@/shared/api'
import { readJson, writeJson } from '@/shared/lib/storage'
import { pluralize } from '@/shared/lib/format'
import { rememberRun, useProject } from '@/entities/project'
import { useActiveProviderSession } from '@/entities/provider-session'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { isAbortError, PlanBuildProgress, requestAndStorePlan, usePlanBuild } from '@/features/edit-deck-plan'
import { AgentChat } from '@/features/agent-chat'
import { AssistantCommandBar } from '@/features/assistant-command'
import { routes } from '@/shared/config'
import { Button, EmptyState, InsightBoardSkeleton, LinkButton } from '@/shared/ui'
import { StepShell } from '@/widgets/step-shell'
import { applyOptimisticBlockOps, applyOptimisticInsightPatch, applyOptimisticNotes, DecisionPanel, DraftDeck, fallbackUnderstandingFromContentPack, type Selection } from '@/widgets/draft-deck'
import { SelectionDetails } from './SelectionDetails'
import styles from './UnderstandingPage.module.css'

interface UndoEntry {
  packId: string
  snapshot: Understanding
}

function errorMessage(error: unknown): string | null {
  if (!error || isAbortError(error)) return null
  return humanError(error)
}

function excludedKey(projectId: string): string {
  return `deckdna.understanding-excluded:${projectId}`
}

function readBriefSlideCount(brief: Record<string, unknown> | undefined | null): number | null {
  if (!brief) return null
  for (const key of ['target_slide_count', 'slide_count', 'requested_slides', 'requested']) {
    const val = brief[key]
    if (typeof val === 'number' && Number.isFinite(val) && val > 0) return val
  }
  return null
}

export function UnderstandingPage() {
  const { projectId = '' } = useParams()
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: project, isLoading: projectLoading, error: projectError } = useProject(projectId)
  const { data: session, isLoading: sessionLoading } = useAssistantSession(projectId)
  const providerSession = useActiveProviderSession()
  const modelAuto = useCapabilityFlag(FEATURE_PATHS.modelAuto)
  const llmAllowed = Boolean(providerSession) || modelAuto
  const useLlm = false

  const initialPackId = session?.artifacts.content_pack_id ?? project?.content_pack_id ?? searchParams.get('pack') ?? null
  const [overridePackId, setOverridePackId] = useState<string | null>(null)
  const activePackId = overridePackId ?? initialPackId
  const templateId = session?.artifacts.template_id ?? project?.template_id ?? null

  const understandingQuery = useUnderstanding(activePackId, { useLlm })
  const unsupportedUnderstanding = understandingQuery.isFetched && understandingQuery.data === null && Boolean(activePackId)
  const { data: legacyPack, isLoading: legacyLoading } = useContentPack(unsupportedUnderstanding ? activePackId : null)

  const [optimistic, setOptimistic] = useState<Understanding | null>(null)
  const [undoStack, setUndoStack] = useState<UndoEntry[]>([])
  const [feedbackNote, setFeedbackNote] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [noteOpen, setNoteOpen] = useState(false)
  const [noteInput, setNoteInput] = useState('')
  const [buildingPlan, setBuildingPlan] = useState(false)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [confirmingAll, setConfirmingAll] = useState(false)
  const [excludedSession, setExcludedSession] = useState<{ packId: string; ids: readonly string[] } | null>(null)

  const editBlocks = useEditBlocks(activePackId ?? '')
  const editInsight = useEditInsight(activePackId ?? '')
  const addNotes = useAddNotes(activePackId ?? '')
  const questionsQuery = useQuestions(projectId)
  const submitAnswers = useSubmitAnswers(projectId)
  const autopilot = useStartAutopilot(projectId)
  const planBuild = usePlanBuild()

  const baseUnderstanding = useMemo(() => {
    if (understandingQuery.data) return understandingQuery.data
    if (unsupportedUnderstanding && legacyPack) return fallbackUnderstandingFromContentPack(legacyPack)
    return null
  }, [understandingQuery.data, unsupportedUnderstanding, legacyPack])

  const currentUnderstanding = optimistic ?? baseUnderstanding

  const storedExcludedIds = useMemo(() => (projectId ? readJson<string[]>(excludedKey(projectId), []) : []), [projectId])
  const excludedInsightIds = excludedSession?.packId === activePackId ? excludedSession.ids : storedExcludedIds

  const setExcludedIds = (ids: readonly string[]) => {
    if (!activePackId) return
    setExcludedSession({ packId: activePackId, ids })
    writeJson(excludedKey(projectId), ids)
  }

  const handleUndo = useCallback(() => {
    setUndoStack((prev) => {
      const last = prev.at(-1)
      if (!last) return prev
      setOverridePackId(last.packId)
      setOptimistic(last.snapshot)
      queryClient.setQueryData([...assistantKeys.understanding(last.packId), { useLlm }], last.snapshot)
      setFeedbackNote('Вернул предыдущий состав материалов.')
      setActionError(null)
      return prev.slice(0, -1)
    })
  }, [queryClient, useLlm])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'z') {
        const target = event.target as HTMLElement | null
        if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
        if (undoStack.length > 0) {
          event.preventDefault()
          handleUndo()
        }
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [undoStack.length, handleUndo])

  const runBlockOps = async (ops: BlockOps['ops'], noteMessage: string) => {
    if (!currentUnderstanding || !activePackId) return
    const prevPackId = activePackId
    const prevSnapshot = currentUnderstanding
    setUndoStack((stack) => [...stack, { packId: prevPackId, snapshot: prevSnapshot }])
    const nextOptimistic = applyOptimisticBlockOps(prevSnapshot, ops)
    setOptimistic(nextOptimistic)
    setFeedbackNote(noteMessage)
    setActionError(null)

    if (unsupportedUnderstanding) return

    try {
      const result = await editBlocks.mutateAsync({ ops })
      if (result?.content_pack_id && result.understanding) {
        queryClient.setQueryData([...assistantKeys.understanding(result.content_pack_id), { useLlm }], result.understanding)
        setOverridePackId(result.content_pack_id)
        setOptimistic(result.understanding)
      }
    } catch (error) {
      const status = (error as { status?: number })?.status
      if (status === 422 && ops.some((op) => op.op === 'move' || op.op === 'merge')) {
        return
      }
      setOptimistic(prevSnapshot)
      setUndoStack((stack) => stack.slice(0, -1))
      setActionError(errorMessage(error))
    }
  }

  const handleRemoveBlock = (blockId: string) => {
    void runBlockOps([{ op: 'remove', block_id: blockId }], 'Обновил состав материалов — вернуть прошлое состояние можно по Ctrl+Z.')
  }

  const handleEditBlock = (blockId: string, patch: { title?: string; text?: string }) => {
    void runBlockOps([{ op: 'edit', block_id: blockId, ...patch }], 'Сохранил вашу формулировку блока.')
  }

  const handlePatchInsight = async (insightId: string, patch: InsightPatch) => {
    if (!currentUnderstanding || !activePackId) return
    const prevSnapshot = currentUnderstanding
    const nextOptimistic = applyOptimisticInsightPatch(prevSnapshot, insightId, patch)
    setOptimistic(nextOptimistic)
    queryClient.setQueryData([...assistantKeys.understanding(activePackId), { useLlm }], nextOptimistic)
    setFeedbackNote(patch.confirmed ? 'Отметил вывод для ключевых слайдов презентации.' : 'Обновил статус вывода.')
    setActionError(null)

    if (unsupportedUnderstanding) return

    try {
      await editInsight.mutateAsync({ insightId, ...patch })
    } catch (error) {
      setOptimistic(prevSnapshot)
      setActionError(errorMessage(error))
    }
  }

  const handleConfirmAllInsights = async () => {
    if (!currentUnderstanding || !activePackId || confirmingAll) return
    const pendingIds = currentUnderstanding.datasets
      .flatMap((dataset) => dataset.insights)
      .filter((insight) => !insight.confirmed && !excludedInsightIds.includes(insight.id))
      .map((insight) => insight.id)
    if (pendingIds.length === 0) return
    const prevSnapshot = currentUnderstanding
    let nextOptimistic = prevSnapshot
    for (const id of pendingIds) nextOptimistic = applyOptimisticInsightPatch(nextOptimistic, id, { confirmed: true })
    setConfirmingAll(true)
    setOptimistic(nextOptimistic)
    queryClient.setQueryData([...assistantKeys.understanding(activePackId), { useLlm }], nextOptimistic)
    setFeedbackNote(`Отметил ${pendingIds.length} ${pluralize(pendingIds.length, ['вывод', 'вывода', 'выводов'])} — они пойдут в ключевые слайды.`)
    setActionError(null)

    if (unsupportedUnderstanding) {
      setConfirmingAll(false)
      return
    }

    try {
      for (const id of pendingIds) {
        await editInsight.mutateAsync({ insightId: id, confirmed: true })
      }
    } catch (error) {
      setOptimistic(prevSnapshot)
      setActionError(errorMessage(error))
    } finally {
      setConfirmingAll(false)
    }
  }

  const handleExcludeInsight = (insightId: string) => {
    if (!activePackId) return
    setExcludedIds(excludedInsightIds.includes(insightId) ? excludedInsightIds : [...excludedInsightIds, insightId])
    if (selection?.kind === 'insight' && selection.id === insightId) setSelection(null)
    const insight = currentUnderstanding?.datasets.flatMap((dataset) => dataset.insights).find((item) => item.id === insightId)
    if (insight?.confirmed || insight?.implication || insight?.needs_confirmation) void handlePatchInsight(insightId, { confirmed: false })
    setFeedbackNote('Не включу этот вывод — вернуть можно внизу таблицы.')
  }

  const handleIncludeInsight = (insightId: string) => {
    if (!activePackId) return
    setExcludedIds(excludedInsightIds.filter((id) => id !== insightId))
    setFeedbackNote('Вернул вывод в презентацию.')
  }

  const handleAddNote = async () => {
    const clean = noteInput.trim()
    if (!clean || !currentUnderstanding || !activePackId) return
    const prevPackId = activePackId
    const prevSnapshot = currentUnderstanding
    setUndoStack((stack) => [...stack, { packId: prevPackId, snapshot: prevSnapshot }])
    setOptimistic(applyOptimisticNotes(prevSnapshot, [clean]))
    setNoteInput('')
    setNoteOpen(false)
    setFeedbackNote('Добавил ваш тезис в состав материалов — отменить можно по Ctrl+Z.')
    setActionError(null)

    if (unsupportedUnderstanding) return

    try {
      const result = await addNotes.mutateAsync({ items: [clean] })
      if (result?.content_pack_id && result.understanding) {
        queryClient.setQueryData([...assistantKeys.understanding(result.content_pack_id), { useLlm }], result.understanding)
        setOverridePackId(result.content_pack_id)
        setOptimistic(result.understanding)
      }
    } catch (error) {
      setOptimistic(prevSnapshot)
      setUndoStack((stack) => stack.slice(0, -1))
      setActionError(errorMessage(error))
    }
  }

  const handleStartAutopilot = async () => {
    setActionError(null)
    try {
      const accepted = await autopilot.mutateAsync({
        from_step: 2,
        use_llm: llmAllowed,
        ...(llmAllowed && providerSession?.id ? { provider_session_id: providerSession.id } : {}),
      })
      if (!accepted) {
        navigate(routes.start(projectId))
        return
      }
      if (accepted.assistant_note) setFeedbackNote(accepted.assistant_note)
      if (accepted.job_id) {
        try {
          const job = await requestJson<{ result_ids?: Record<string, string> }>(`/jobs/${encodeURIComponent(accepted.job_id)}`)
          const genId = job.result_ids?.generation_id
          if (genId) {
            rememberRun(projectId, genId)
            navigate(routes.run(projectId, genId))
            return
          }
        } catch {
          void 0
        }
        navigate(routes.run(projectId, accepted.job_id))
      }
    } catch (error) {
      setActionError(errorMessage(error))
    }
  }

  const handleContinue = async (overrideSlideCount?: number) => {
    setActionError(null)
    if (unsupportedUnderstanding && !questionsQuery.data && overrideSlideCount === undefined) {
      navigate(routes.start(projectId))
      return
    }
    const qData = questionsQuery.data
    const forceBuild = typeof overrideSlideCount === 'number' && Number.isFinite(overrideSlideCount)
    if (forceBuild || (qData && qData.questions.length === 0)) {
      if (session) {
        queryClient.setQueryData(assistantKeys.session(projectId), {
          ...session,
          step: 4,
          completed_steps: [...new Set([...session.completed_steps, 1, 2])],
          skipped_steps: [...new Set([...session.skipped_steps, 3])],
          brief: {
            ...(session.brief ?? {}),
            ...(forceBuild ? { target_slide_count: overrideSlideCount, content_fill: 'shrink' } : {}),
          },
        })
      }
      if (templateId && activePackId) {
        setBuildingPlan(true)
        const options = planBuild.begin()
        planBuild.enterPhase('plan')
        try {
          const answered = await submitAnswers.mutateAsync({
            answers: forceBuild ? { target_slide_count: overrideSlideCount, content_fill: 'shrink' } : {},
          })
          const mergedBrief: Record<string, unknown> = {
            ...(session?.brief ?? {}),
            ...(currentUnderstanding?.brief ?? {}),
            ...(qData?.suggested_brief ?? {}),
            ...(answered?.brief ?? {}),
            ...(forceBuild ? { target_slide_count: overrideSlideCount, content_fill: 'shrink' } : {}),
          }
          const pickMeaningful = (...values: unknown[]): string | null => {
            for (const val of values) {
              if (typeof val === 'string' && val.trim() && val.trim().toLowerCase() !== 'presentation') {
                return val.trim()
              }
            }
            return null
          }
          const purpose =
            pickMeaningful(
              answered?.brief?.purpose,
              qData?.suggested_brief?.purpose,
              currentUnderstanding?.brief?.purpose,
              session?.brief?.purpose,
            ) ?? 'presentation'
          const keyMessage = pickMeaningful(
            answered?.brief?.key_message,
            qData?.suggested_brief?.key_message,
            currentUnderstanding?.brief?.key_message,
            session?.brief?.key_message,
            purpose !== 'presentation' ? purpose : null,
          )
          await requestAndStorePlan(
            projectId,
            {
              template_id: templateId,
              content_pack_id: activePackId,
              brief: {
                purpose,
                audience: String(mergedBrief.audience ?? ''),
                language: String(mergedBrief.language || project?.default_language || 'ru'),
                target_slide_count: forceBuild ? overrideSlideCount : Number(mergedBrief.target_slide_count || project?.target_slide_count || 8),
                ...(keyMessage ? { key_message: keyMessage } : {}),
                ...(forceBuild
                  ? { content_fill: 'shrink' }
                  : mergedBrief.content_fill
                    ? { content_fill: mergedBrief.content_fill as 'expand' | 'shrink' | 'ask' }
                    : {}),
              },
              strategies: ['balanced'],
              use_llm: llmAllowed,
              ...(llmAllowed && providerSession?.id ? { provider_session_id: providerSession.id } : {}),
            },
            { keyMessage, ...(forceBuild ? { contentFill: 'keep' } : {}) },
            options,
          )
        } catch {
          void 0
        } finally {
          planBuild.finish()
          setBuildingPlan(false)
        }
      }
      navigate(routes.planDraft(projectId))
      return
    }
    navigate(routes.questions(projectId))
  }

  const isLoading = projectLoading || sessionLoading || (Boolean(activePackId) && understandingQuery.isLoading) || (unsupportedUnderstanding && legacyLoading)
  const title = 'Вот что я нашёл'

  const understandingContentFill = useMemo(
    () =>
      !unsupportedUnderstanding && currentUnderstanding
        ? parseContentFill((currentUnderstanding as unknown as Record<string, unknown>).content_fill, currentUnderstanding)
        : null,
    [unsupportedUnderstanding, currentUnderstanding],
  )
  const capacitySlides =
    understandingContentFill?.capacity ??
    (!unsupportedUnderstanding &&
    typeof currentUnderstanding?.summary?.capacity_slides === 'number' &&
    Number.isFinite(currentUnderstanding.summary.capacity_slides)
      ? currentUnderstanding.summary.capacity_slides
      : null)
  const requestedSlides =
    understandingContentFill?.requested ??
    readBriefSlideCount(currentUnderstanding?.brief) ??
    readBriefSlideCount(session?.brief) ??
    null
  const hasShortage =
    capacitySlides !== null &&
    requestedSlides !== null &&
    capacitySlides > 0 &&
    capacitySlides < requestedSlides

  if (projectError) {
    return <EmptyState title="Не удалось открыть проект" description={errorMessage(projectError) ?? 'Не удалось открыть проект.'} />
  }

  if (!activePackId && !isLoading) {
    return (
      <StepShell
        title={title}
        note="Сначала добавьте материалы или напишите, о чём презентация."
        canvasLabel="Черновик презентации"
        canvas={null}
        actions={
          <Button variant="primary" size="lg" onClick={() => navigate(routes.start(projectId))}>
            К материалам
          </Button>
        }
      />
    )
  }

  const understanding = currentUnderstanding
  const busy = buildingPlan || autopilot.isPending
  const capacity = understanding?.summary.capacity_slides ?? 0
  const note = buildingPlan
    ? 'Составляю план презентации…'
    : (feedbackNote ??
      (understanding
        ? `${capacity > 0 ? `Хватит примерно на ${capacity} ${pluralize(capacity, ['слайд', 'слайда', 'слайдов'])}. ` : ''}Отметьте выводы, которые пойдут в презентацию, лишние уберите.`
        : 'Разбираю материалы…'))

  return (
    <StepShell
      title={title}
      note={note}
      canvasLabel="Черновик презентации"
      canvas={
        buildingPlan ? (
          <div className={styles.progressStage}>
            <PlanBuildProgress progress={planBuild.progress} phase={planBuild.phase} startedAt={planBuild.startedAt} onCancel={planBuild.cancel} />
          </div>
        ) : isLoading || !understanding ? (
          <InsightBoardSkeleton />
        ) : (
          <DraftDeck
            understanding={understanding}
            selection={selection}
            onSelect={setSelection}
            onPatchInsight={(id, patch) => void handlePatchInsight(id, patch)}
            onExcludeInsight={handleExcludeInsight}
            onIncludeInsight={handleIncludeInsight}
            onRemoveBlock={handleRemoveBlock}
            excludedInsightIds={excludedInsightIds}
          />
        )
      }
      inspector={
        selection && understanding
          ? {
              title: selection.kind === 'insight' ? 'Вывод' : 'Блок из текста',
              onClose: () => setSelection(null),
              content: (
                <SelectionDetails
                  key={`${selection.kind}-${selection.id}-${selection.edit ? 'edit' : 'view'}`}
                  understanding={understanding}
                  selection={selection}
                  onPatchInsight={(id, patch) => void handlePatchInsight(id, patch)}
                  onEditBlock={handleEditBlock}
                  onRemoveBlock={(id) => {
                    handleRemoveBlock(id)
                    setSelection(null)
                  }}
                  onEditModeChange={(edit) =>
                    setSelection((prev) => (prev ? { ...prev, edit } : prev))
                  }
                />
              ),
            }
          : null
      }
      command={<AgentChat projectId={projectId} fallback={<AssistantCommandBar projectId={projectId} label="Попросить словами" placeholder="Например: убери выводы про возраст" />} />}
      footnote={buildingPlan ? undefined : "Дальше — составлю план, вы его проверите. Сделай сам — соберу презентацию без остановок."}
      actions={
        <>
          <Button variant="primary" size="lg" pending={buildingPlan} disabled={busy || !understanding} onClick={() => void handleContinue()}>
            Дальше
          </Button>
          <Button variant="ghost" size="lg" pending={autopilot.isPending} disabled={busy || !understanding} onClick={() => void handleStartAutopilot()}>
            Сделай сам
          </Button>
        </>
      }
    >
      {!buildingPlan && (
        <>
          {hasShortage && capacitySlides !== null && requestedSlides !== null && (
            <div className={styles.shortageBanner} role="status">
              <p className={styles.shortageText}>
                Материалов хватит примерно на {capacitySlides} {pluralize(capacitySlides, ['слайд', 'слайда', 'слайдов'])} из {requestedSlides}.
              </p>
              {understandingContentFill?.shortageReason && (
                <p className={styles.shortageHint}>{understandingContentFill.shortageReason}</p>
              )}
              <div className={styles.shortageActions}>
                <Button variant="secondary" size="sm" disabled={busy} onClick={() => navigate(routes.start(projectId))}>
                  Добавить файлы
                </Button>
                <Button variant="secondary" size="sm" disabled={busy} onClick={() => navigate(routes.questions(projectId))}>
                  Ответить на вопросы
                </Button>
                <Button variant="secondary" size="sm" disabled={busy} onClick={() => void handleContinue(capacitySlides)}>
                  Собрать {capacitySlides} {pluralize(capacitySlides, ['слайд', 'слайда', 'слайдов'])}
                </Button>
              </div>
            </div>
          )}
          {understanding && (
            <DecisionPanel
              understanding={understanding}
              excludedInsightIds={excludedInsightIds}
              onConfirmAll={() => void handleConfirmAllInsights()}
              confirmAllPending={confirmingAll}
            />
          )}
          <section className={styles.thesis} aria-labelledby="own-thesis-title">
            <h2 id="own-thesis-title" className={styles.thesisTitle}>
              Свой тезис
            </h2>
            {!noteOpen && <p className={styles.thesisHint}>Если важного факта нет в файлах, добавьте его — он попадёт в презентацию.</p>}
          {noteOpen ? (
            <div className={styles.noteForm}>
              <label className={styles.noteLabel}>
                <span className={styles.visuallyHidden}>Текст тезиса</span>
                <textarea
                  className={styles.noteInput}
                  rows={3}
                  value={noteInput}
                  autoFocus
                  placeholder="Например, команда выросла до 40 человек"
                  onChange={(event) => setNoteInput(event.target.value)}
                />
              </label>
              <div className={styles.noteActions}>
                <Button variant="secondary" size="md" disabled={!noteInput.trim()} onClick={() => void handleAddNote()}>
                  Добавить
                </Button>
                <Button variant="ghost" size="md" onClick={() => setNoteOpen(false)}>
                  Отмена
                </Button>
              </div>
            </div>
          ) : (
            <LinkButton onClick={() => setNoteOpen(true)}>
              Добавить свой тезис
            </LinkButton>
          )}
          </section>
          {undoStack.length > 0 && (
            <LinkButton onClick={handleUndo}>
              Отменить последнее изменение
            </LinkButton>
          )}
          {(actionError || errorMessage(understandingQuery.error)) && (
            <p className={styles.error} role="alert">
              {actionError ?? errorMessage(understandingQuery.error)}
            </p>
          )}
        </>
      )}
    </StepShell>
  )
}
