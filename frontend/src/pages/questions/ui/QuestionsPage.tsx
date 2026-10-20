import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Navigate, useNavigate, useParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import {
  assistantKeys,
  useAddNotes,
  useAssistantSession,
  useQuestions,
  useStartAutopilot,
  useSubmitAnswers,
  useUnderstanding,
  type Question,
} from '@/entities/assistant'
import { isApiError, requestJson, humanError } from '@/shared/api'
import { rememberRun, useInvalidateProject, useProject } from '@/entities/project'
import { useActiveProviderSession } from '@/entities/provider-session'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { isAbortError, PlanBuildProgress, requestAndStorePlan, usePlanBuild } from '@/features/edit-deck-plan'
import { routes } from '@/shared/config'
import { Button, ChoiceListSkeleton, DeckSpecSkeleton, EmptyState, LinkButton } from '@/shared/ui'
import { StepShell } from '@/widgets/step-shell'
import {
  buildAnswersPayload,
  buildDeckSpec,
  cleanThesisNotes,
  initialSelections,
  questionTitle,
  isContentShortQuestion,
  mapShortChoiceToFillMode,
  normalizeAnsweredBrief,
  normalizeContentShortOptions,
  resolveQuestionsNote,
  visibleQuestions,
  type QuestionSelection,
} from '../lib/questions'
import { Choices } from './Choices'
import { DeckSpec } from './DeckSpec'
import styles from './QuestionsPage.module.css'

function errorMessage(error: unknown): string | null {
  if (!error || isAbortError(error)) return null
  return humanError(error)
}

function isEditableTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable
}

export function QuestionsPage() {
  const { projectId = '' } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const invalidateProject = useInvalidateProject()

  const { data: project, isLoading: projectLoading, error: projectError } = useProject(projectId)
  const { data: session, isLoading: sessionLoading } = useAssistantSession(projectId)
  const providerSession = useActiveProviderSession()
  const modelAuto = useCapabilityFlag(FEATURE_PATHS.modelAuto)
  const useLlm = Boolean(providerSession) || modelAuto

  const questionsQuery = useQuestions(projectId)
  const submitAnswers = useSubmitAnswers(projectId)
  const activePackId = session?.artifacts.content_pack_id ?? project?.content_pack_id ?? null
  const templateId = session?.artifacts.template_id ?? project?.template_id ?? null
  const addNotes = useAddNotes(activePackId ?? '')
  const autopilot = useStartAutopilot(projectId)
  const planBuild = usePlanBuild()
  const understanding = useUnderstanding(activePackId, { useLlm })

  const rawQuestions = useMemo(() => {
    const qList = questionsQuery.data?.questions ?? []
    const suggested = questionsQuery.data?.suggested_brief
    if (
      qList.length > 0 &&
      !qList.some(isContentShortQuestion) &&
      suggested?.content_fill === 'ask'
    ) {
      const syntheticShort: Question = {
        id: 'content_short',
        kind: 'content_short',
        reason: 'Материала меньше, чем запрошено слайдов — выберите, как поступить.',
        options: normalizeContentShortOptions([]),
        allow_custom: false,
        optional: false,
      }
      return [syntheticShort, ...qList]
    }
    return qList
  }, [questionsQuery.data])

  const questions = useMemo(() => visibleQuestions(rawQuestions), [rawQuestions])
  const [userSelections, setUserSelections] = useState<Record<string, QuestionSelection>>({})
  const selections = useMemo(
    () => ({ ...initialSelections(questions), ...userSelections }),
    [questions, userSelections],
  )
  const [skippedIds, setSkippedIds] = useState<Set<string>>(() => new Set())
  const [thesisNotes, setThesisNotes] = useState<[string, string, string]>(['', '', ''])
  const [feedbackNote, setFeedbackNote] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [buildingPlan, setBuildingPlan] = useState(false)
  const [activeQuestionId, setActiveQuestionId] = useState<string | null>(null)
  const autoSkippedRef = useRef(false)

  const suggestedBrief = questionsQuery.data?.suggested_brief
  const defaultLanguage = project?.default_language ?? 'ru'
  const targetSlideCount = project?.target_slide_count ?? 8
  const providerSessionId = providerSession?.id ?? null

  const runBuildPlan = useCallback(
    async (answersPayload: Record<string, string | number>, notesToSubmit: readonly string[]) => {
      autoSkippedRef.current = true
      setActionError(null)
      setBuildingPlan(true)
      const options = planBuild.begin()
      try {
        let packIdToUse = activePackId
        const cleanNotes = cleanThesisNotes(notesToSubmit)
        if (cleanNotes.length > 0 && packIdToUse) {
          const noteResult = await addNotes.mutateAsync({ items: cleanNotes })
          if (noteResult?.content_pack_id) {
            packIdToUse = noteResult.content_pack_id
          }
        }

        const answered = await submitAnswers.mutateAsync({ answers: answersPayload })
        if (answered?.assistant_note) {
          setFeedbackNote(answered.assistant_note)
        }

        if (templateId && packIdToUse) {
          planBuild.enterPhase('plan')
          const brief = normalizeAnsweredBrief(
            answered?.brief,
            suggestedBrief,
            defaultLanguage,
            targetSlideCount,
          )
          const shortAnswer = typeof answersPayload.content_short === 'string' ? answersPayload.content_short : undefined
          const contentFill = mapShortChoiceToFillMode(shortAnswer)
          const baseRequest = {
            template_id: templateId,
            content_pack_id: packIdToUse,
            brief,
            strategies: ['balanced' as const],
            use_llm: useLlm,
            ...(useLlm && providerSessionId ? { provider_session_id: providerSessionId } : {}),
          }
          try {
            await requestAndStorePlan(projectId, baseRequest, { contentFill }, options)
          } catch (planErr) {
            if (isApiError(planErr) && planErr.status === 422) {
              await requestAndStorePlan(
                projectId,
                {
                  ...baseRequest,
                  brief: {
                    ...brief,
                    purpose: brief.purpose || 'product',
                    audience: brief.audience || 'руководство',
                  },
                },
                { contentFill },
                options,
              )
            } else {
              throw planErr
            }
          }
        }

        void invalidateProject(projectId)
        navigate(routes.planDraft(projectId), { replace: questions.length === 0 })
      } catch (error) {
        const msg = errorMessage(error)
        if (msg) setActionError(msg)
      } finally {
        planBuild.finish()
        setBuildingPlan(false)
      }
    },
    [
      activePackId,
      addNotes,
      defaultLanguage,
      invalidateProject,
      navigate,
      planBuild,
      projectId,
      providerSessionId,
      questions.length,
      submitAnswers,
      suggestedBrief,
      targetSlideCount,
      templateId,
      useLlm,
    ],
  )

  useEffect(() => {
    if (
      !projectLoading &&
      !sessionLoading &&
      !buildingPlan &&
      !submitAnswers.isPending &&
      questionsQuery.isSuccess &&
      questionsQuery.data !== null &&
      questionsQuery.data.questions.length === 0 &&
      !session?.artifacts.plan_id &&
      !autoSkippedRef.current
    ) {
      autoSkippedRef.current = true
      if (session) {
        queryClient.setQueryData(assistantKeys.session(projectId), {
          ...session,
          step: 4,
          completed_steps: [...new Set([...session.completed_steps, 1, 2])],
          skipped_steps: [...new Set([...session.skipped_steps, 3])],
        })
      }
      void runBuildPlan({}, [])
    }
  }, [
    projectLoading,
    sessionLoading,
    buildingPlan,
    submitAnswers.isPending,
    questionsQuery.isSuccess,
    questionsQuery.data,
    session,
    queryClient,
    projectId,
    runBuildPlan,
  ])

  const handleSelectOption = useCallback((questionId: string, value: string) => {
    setUserSelections((prev) => ({
      ...prev,
      [questionId]: { mode: 'option', value, customText: prev[questionId]?.customText ?? '' },
    }))
    setSkippedIds((prev) => {
      if (!prev.has(questionId)) return prev
      const next = new Set(prev)
      next.delete(questionId)
      return next
    })
  }, [])

  const handleSelectCustom = useCallback((questionId: string) => {
    setUserSelections((prev) => ({
      ...prev,
      [questionId]: { mode: 'custom', value: prev[questionId]?.value ?? '', customText: prev[questionId]?.customText ?? '' },
    }))
  }, [])

  const handleCustomTextChange = (questionId: string, customText: string) => {
    setUserSelections((prev) => ({
      ...prev,
      [questionId]: { mode: 'custom', value: prev[questionId]?.value ?? '', customText },
    }))
  }

  const handleThesisNoteChange = (slotIndex: 0 | 1 | 2, text: string) => {
    setThesisNotes((prev) => {
      const next: [string, string, string] = [prev[0], prev[1], prev[2]]
      next[slotIndex] = text
      return next
    })
  }

  const loading = projectLoading || sessionLoading || questionsQuery.isLoading
  const busy = buildingPlan || submitAnswers.isPending || addNotes.isPending || autopilot.isPending

  useEffect(() => {
    if (loading || busy || questions.length === 0) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (isEditableTarget(event.target)) return
      if (event.key < '1' || event.key > '5') return
      const targetQuestion =
        questions.find((q) => q.id === activeQuestionId && !skippedIds.has(q.id)) ??
        questions.find((q) => !skippedIds.has(q.id))
      if (!targetQuestion) return
      const idx = Number(event.key) - 1
      if (idx < targetQuestion.options.length) {
        const opt = targetQuestion.options[idx]
        if (opt) {
          event.preventDefault()
          handleSelectOption(targetQuestion.id, opt.value)
        }
      } else if (idx === targetQuestion.options.length && targetQuestion.allow_custom) {
        event.preventDefault()
        handleSelectCustom(targetQuestion.id)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [activeQuestionId, busy, handleSelectCustom, handleSelectOption, loading, questions, skippedIds])

  const deckSpec = useMemo(
    () =>
      buildDeckSpec({
        questions,
        selections,
        skippedIds,
        suggestedBrief,
        understanding: understanding.data ?? null,
        thesisNotes,
        fallbackSlideCount: targetSlideCount,
      }),
    [questions, selections, skippedIds, suggestedBrief, understanding.data, thesisNotes, targetSlideCount],
  )

  const handleSubmit = () => {
    const payload = buildAnswersPayload(questions, selections, skippedIds)
    const shortVal = typeof payload.content_short === 'string' ? payload.content_short : null
    const notes = shortVal === 'notes' ? thesisNotes : []
    void runBuildPlan(payload, notes)
  }

  const handleSkipAll = () => {
    void runBuildPlan({}, [])
  }

  const handleStartAutopilot = async () => {
    setActionError(null)
    try {
      const accepted = await autopilot.mutateAsync({
        from_step: 3,
        use_llm: useLlm,
        ...(useLlm && providerSession?.id ? { provider_session_id: providerSession.id } : {}),
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

  if (!buildingPlan && session?.artifacts.plan_id && questionsQuery.data?.questions.length === 0) {
    return <Navigate to={routes.planDraft(projectId)} replace />
  }

  if (projectError) {
    return <EmptyState title="Не удалось открыть проект" description={errorMessage(projectError) ?? 'Не удалось открыть проект.'} />
  }

  const single = questions.length === 1
  const first = questions[0]
  const title = buildingPlan ? 'Составляю план' : single && first ? questionTitle(first) : 'Пара уточнений'
  const shortSelected = questions.some((question) => isContentShortQuestion(question) && selections[question.id]?.value === 'notes')
  const note = buildingPlan
    ? 'Собираю структуру по вашим ответам…'
    : single
      ? (questions[0]?.reason ?? resolveQuestionsNote(questionsQuery.data, session?.assistant_note, null, feedbackNote))
      : resolveQuestionsNote(questionsQuery.data, session?.assistant_note, shortSelected ? 'notes' : null, feedbackNote)

  return (
    <StepShell
      title={title}
      note={note}
      canvasLabel={buildingPlan ? 'Построение плана' : 'Вопросы и спецификация колоды'}
      canvas={
        buildingPlan ? (
          <div className={styles.progressStage}>
            <PlanBuildProgress progress={planBuild.progress} phase={planBuild.phase} startedAt={planBuild.startedAt} onCancel={planBuild.cancel} />
          </div>
        ) : (
          <div className={styles.board}>
            <div className={styles.questionsCol}>
              {loading && <ChoiceListSkeleton count={4} />}
              {!loading &&
                questions.map((question) => (
                  <section className={styles.questionPanel} key={question.id}>
                    <Choices
                      question={question}
                      heading={single ? null : questionTitle(question)}
                      reason={single ? null : question.reason}
                      selection={selections[question.id]}
                      skipped={skippedIds.has(question.id)}
                      disabled={busy}
                      notes={thesisNotes}
                      onActivate={() => setActiveQuestionId(question.id)}
                      onOption={(value) => handleSelectOption(question.id, value)}
                      onCustom={() => handleSelectCustom(question.id)}
                      onCustomText={(text) => handleCustomTextChange(question.id, text)}
                      onNote={handleThesisNoteChange}
                    />
                  </section>
                ))}
            </div>
            <aside className={styles.specCol} aria-label="Как будет выглядеть колода">
              {loading ? <DeckSpecSkeleton /> : <DeckSpec spec={deckSpec} />}
            </aside>
          </div>
        )
      }
      footnote={buildingPlan ? undefined : 'Ответы сразу видны в спецификации справа. Цифры 1–5 на клавиатуре выбирают вариант.'}
      actions={
        <>
          <Button variant="primary" size="lg" pending={buildingPlan} disabled={busy || loading} onClick={handleSubmit}>
            Составить план
          </Button>
          <Button
            variant="ghost"
            size="lg"
            pending={autopilot.isPending}
            disabled={busy || loading}
            onClick={() => void handleStartAutopilot()}
          >
            Сделай сам
          </Button>
        </>
      }
    >
      {!loading && !buildingPlan && questions.length > 0 && (
        <LinkButton onClick={handleSkipAll} disabled={busy}>
          Пропустить и составить план
        </LinkButton>
      )}
      {actionError && (
        <p className={styles.error} role="alert">
          {actionError}
        </p>
      )}
    </StepShell>
  )
}
