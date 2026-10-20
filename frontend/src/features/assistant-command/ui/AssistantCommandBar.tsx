import { useId, useRef, useState } from 'react'
import {
  useAssistantCommand,
  useJobEvents,
  type AssistantCommandRequest,
  type AssistantCommandResult,
  type CommandOption,
} from '@/entities/assistant'
import { humanError } from '@/shared/api'
import { Button } from '@/shared/ui'
import {
  formatAcceptedSummary,
  formatClarificationQuestion,
  loadCommandHistory,
  pushCommandHistory,
  resolveClarificationOptions,
  resolveCommandSlideId,
  saveCommandHistory,
  type CommandHistoryEntry,
  type CommandSlideTarget,
} from '../model/commandModel'
import styles from './AssistantCommandBar.module.css'

const VISIBLE_HISTORY = 2

export interface AssistantCommandBarProps {
  projectId: string
  variantId?: string | null
  slideId?: string | null
  planId?: string | null
  expectedRevision?: number | null
  expectedContextRevision?: number | null
  slides?: readonly CommandSlideTarget[] | null
  onApplied?: (result: AssistantCommandResult) => void
  label?: string
  placeholder?: string
}

interface ClarificationState {
  question: string
  options: CommandOption[]
}

export function AssistantCommandBar({
  projectId,
  variantId,
  slideId,
  planId,
  expectedRevision,
  expectedContextRevision,
  slides,
  onApplied,
  label = 'Скажи, что поменять',
  placeholder = 'Например: короче на слайде 3',
}: AssistantCommandBarProps) {
  const inputId = useId()
  const seqRef = useRef(0)
  const command = useAssistantCommand(projectId)
  const [text, setText] = useState('')
  const [unsupported, setUnsupported] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [clarification, setClarification] = useState<ClarificationState | null>(null)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)
  const [historyState, setHistoryState] = useState<{ projectId: string; entries: CommandHistoryEntry[] }>(() => ({
    projectId,
    entries: loadCommandHistory(projectId),
  }))
  const history = historyState.projectId === projectId ? historyState.entries : loadCommandHistory(projectId)
  const jobEvents = useJobEvents(activeJobId)

  if (unsupported || !projectId) return null

  const recordHistory = (entry: CommandHistoryEntry) => {
    setHistoryState((current) => {
      const base = current.projectId === projectId ? current.entries : loadCommandHistory(projectId)
      const entries = pushCommandHistory(base, entry)
      saveCommandHistory(projectId, entries)
      return { projectId, entries }
    })
  }

  const submitCommand = async (rawText: string) => {
    const cleanText = rawText.trim()
    if (!cleanText || command.isPending) return
    setErrorMessage(null)
    const resolvedSlideId = resolveCommandSlideId(cleanText, slides, slideId)
    const body: AssistantCommandRequest = {
      text: cleanText,
      ...(variantId ? { variant_id: variantId } : {}),
      ...(resolvedSlideId ? { slide_id: resolvedSlideId } : {}),
      ...(planId ? { plan_id: planId } : {}),
      ...(typeof expectedRevision === 'number' ? { expected_revision: expectedRevision } : {}),
      ...(typeof expectedContextRevision === 'number'
        ? { expected_context_revision: expectedContextRevision }
        : {}),
      use_llm: false,
    }
    try {
      const result = await command.mutateAsync(body)
      if (result === null) {
        setUnsupported(true)
        return
      }
      setText('')
      if (result.status === 'clarification') {
        const question = formatClarificationQuestion(result)
        const options = resolveClarificationOptions(result, {
          hasVariant: Boolean(variantId),
          hasPlan: Boolean(planId),
        })
        setClarification({ question, options })
        recordHistory({
          id: `${Date.now()}-${++seqRef.current}`,
          text: cleanText,
          status: 'clarification',
          summary: question,
          jobId: null,
        })
        return
      }
      setClarification(null)
      const summary = formatAcceptedSummary(result)
      const jobId = result.change?.job_id ?? null
      if (jobId) setActiveJobId(jobId)
      recordHistory({
        id: `${Date.now()}-${++seqRef.current}`,
        text: cleanText,
        status: result.status,
        summary,
        jobId,
      })
      onApplied?.(result)
    } catch (error) {
      setErrorMessage(humanError(error))
    }
  }

  const jobRunning = Boolean(activeJobId) && !jobEvents.done && !jobEvents.error
  const jobLabel = jobRunning ? (jobEvents.lastEvent?.message ?? 'Применяю изменения…') : null
  const visibleHistory = (clarification && history[0]?.status === 'clarification' ? history.slice(1) : history).slice(0, VISIBLE_HISTORY)

  return (
    <div className={styles.root} data-panel={Boolean(variantId) || undefined} data-testid="assistant-command-bar">
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault()
          void submitCommand(text)
        }}
      >
        <label htmlFor={inputId} className={styles.label}>
          {label}
        </label>
        <div className={styles.row}>
          <input
            id={inputId}
            type="text"
            className={styles.input}
            value={text}
            disabled={command.isPending}
            placeholder={placeholder}
            onChange={(event) => setText(event.target.value)}
          />
          <Button
            type="submit"
            size="sm"
            variant="secondary"
            disabled={!text.trim() || command.isPending}
            aria-busy={command.isPending || undefined}
          >
            {command.isPending ? 'Отправляю…' : 'Отправить'}
          </Button>
        </div>
      </form>

      {errorMessage && (
        <p className={styles.error} role="alert">
          {errorMessage}
        </p>
      )}

      {clarification && (
        <div className={styles.clarification} role="status">
          <p className={styles.question}>{clarification.question}</p>
          {clarification.options.length > 0 && (
            <div className={styles.options} role="group" aria-label="Варианты ответа">
              {clarification.options.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={styles.option}
                  disabled={command.isPending}
                  onClick={() => void submitCommand(option.value)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {jobLabel && (
        <p className={styles.jobStatus} role="status" aria-live="polite">
          {jobLabel}
        </p>
      )}

      {visibleHistory.length > 0 && (
        <ul className={styles.history} aria-label="Последние команды">
          {visibleHistory.map((entry) => (
            <li key={entry.id} className={styles.historyItem}>
              <span className={styles.historyCommand}>{entry.text}</span>
              <span className={styles.historyResult}>{entry.summary}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
