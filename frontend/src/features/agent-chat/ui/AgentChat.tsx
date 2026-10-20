import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router'
import { MessageCircle, Send, Square, X, RotateCcw, Plus } from 'lucide-react'
import {
  useAgentThread,
  useSendAgentMessage,
  useCancelAgentMessage,
  useClearAgentThread,
  useAgentRun,
  type AgentMessage,
} from '@/entities/assistant'
import { useActiveProviderSession } from '@/entities/provider-session'
import { ApiError, humanError } from '@/shared/api'
import { Button } from '@/shared/ui'
import { AgentActions } from './AgentActions'
import styles from './AgentChat.module.css'

interface ChatSlide {
  id: string
  slide_plan_id?: string | null
  index?: number | null
}
export interface AgentChatProps {
  projectId: string
  variantId?: string | null
  planId?: string | null
  slideId?: string | null
  slides?: readonly ChatSlide[] | null
  fallback?: ReactNode
  onApplied?: (message: AgentMessage) => void | Promise<void>
}
const deckSuggestions = [
  'Что улучшить перед питчем?',
  'Найди свежие цифры по рынку',
  'Добавь слайд про команду',
]
const slideSuggestions = ['Сделай этот слайд короче', 'Покажи данные графиком', 'Что улучшить перед питчем?']

export function AgentChat(props: AgentChatProps) {
  return <ChatPanel key={props.projectId} {...props} />
}

function ChatPanel({ projectId, variantId, planId, slideId, slides, fallback, onApplied }: AgentChatProps) {
  const thread = useAgentThread(projectId)
  const send = useSendAgentMessage(projectId)
  const cancel = useCancelAgentMessage(projectId)
  const clear = useClearAgentThread(projectId)
  const provider = useActiveProviderSession()
  const [params] = useSearchParams()
  const providerParams = new URLSearchParams(params)
  providerParams.set('panel', 'provider')
  const [text, setText] = useState('')
  const [unsupported, setUnsupported] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [context, setContext] = useState({ slideId, detached: false })
  if (context.slideId !== slideId) setContext({ slideId, detached: false })
  const currentSlideId = context.slideId === slideId && !context.detached ? slideId : null
  const input = useRef<HTMLTextAreaElement>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const handled = useRef<string | null>(null)
  const wasBusy = useRef(false)
  const jobId = thread.data?.active_job_id
  const run = useAgentRun(jobId, projectId)
  const busy = send.isPending || Boolean(jobId)
  const items = (thread.data?.items ?? []).filter((item) => item.role !== 'assistant' || item.job_id !== jobId)
  const lastAssistant = items.findLast((item) => item.role === 'assistant')
  const canUndo = lastAssistant?.actions?.some((action) => action.ok && typeof action.revision === 'number') ?? false
  const zeroBased = slides?.some((slide) => slide.index === 0)
  const slideIndex =
    slides?.findIndex((slide) => slide.id === currentSlideId || slide.slide_plan_id === currentSlideId) ?? -1
  const selectedSlide = slides?.[slideIndex]
  const slideNumber =
    selectedSlide && typeof selectedSlide.index === 'number'
      ? selectedSlide.index + (zeroBased ? 1 : 0)
      : slideIndex >= 0
        ? slideIndex + 1
        : null
  const suggestions = currentSlideId ? slideSuggestions : deckSuggestions

  useEffect(() => {
    if (jobId && handled.current === null) handled.current = ''
  }, [jobId])
  useEffect(() => {
    bottom.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' })
  }, [items.length, run.steps.length, run.status, run.lastSequence])
  useEffect(() => {
    if (wasBusy.current && !busy) input.current?.focus()
    wasBusy.current = busy
  }, [busy])
  useEffect(() => {
    if (!lastAssistant || jobId || handled.current === lastAssistant.id) return
    const previous = handled.current
    handled.current = lastAssistant.id
    if (previous !== null && lastAssistant.actions?.some((action) => action.ok)) {
      void Promise.resolve(onApplied?.(lastAssistant)).catch((error: unknown) => setError(humanError(error)))
    }
  }, [lastAssistant, jobId, onApplied])

  const submit = async (raw: string) => {
    const clean = raw.trim()
    if (!clean || busy || clear.isPending || thread.isPending) return
    if (handled.current === null) handled.current = lastAssistant?.id ?? ''
    setError(null)
    try {
      const accepted = await send.mutateAsync({
        text: clean,
        ...(variantId ? { variant_id: variantId } : {}),
        ...(planId ? { plan_id: planId } : {}),
        ...(currentSlideId ? { slide_id: currentSlideId } : {}),
        ...(provider?.id ? { provider_session_id: provider.id } : {}),
      })
      if (!accepted) {
        setUnsupported(true)
        return
      }
      setText('')
    } catch (error) {
      setError(
        error instanceof ApiError && error.status === 409 ? 'Ещё отвечаю на прошлое сообщение' : humanError(error),
      )
    }
  }
  const stop = async () => {
    if (!jobId) return
    setError(null)
    try {
      if ((await cancel.mutateAsync(jobId)) === null) setUnsupported(true)
    } catch (error) {
      setError(humanError(error))
    }
  }
  const newConversation = async () => {
    setError(null)
    try {
      if ((await clear.mutateAsync()) === null) {
        setUnsupported(true)
        return
      }
      handled.current = null
      setText('')
      input.current?.focus()
    } catch (error) {
      setError(humanError(error))
    }
  }

  if (unsupported || run.unsupported || thread.data === null) return <>{fallback}</>

  return (
    <section
      className={styles.root}
      aria-label="Помощник"
      data-testid="agent-chat"
      data-panel={Boolean(variantId) || undefined}
    >
      <header className={styles.header}>
        <div className={styles.headerTitle}>
          <span className={styles.headerIcon} aria-hidden="true">
            <MessageCircle size={15} />
          </span>
          <h3>Помощник</h3>
        </div>
        <button
          type="button"
          className={styles.new}
          disabled={busy || clear.isPending || !items.length}
          onClick={() => void newConversation()}
          title="Новый разговор"
          aria-label="Новый разговор"
        >
          <Plus size={14} />
          Новый разговор
        </button>
      </header>
      <div className={styles.feed} role="log" aria-label="Разговор с помощником" aria-live="polite">
        {thread.isPending && <p className={styles.muted}>Открываю разговор…</p>}
        {!thread.isPending && items.length === 0 && !jobId && (
          <div className={styles.welcome}>
            <p>Обсудим презентацию?</p>
            <span>{currentSlideId
              ? `Работаю ${slideNumber !== null ? `со слайдом ${slideNumber}` : 'с открытым слайдом'}. Выберите правку или напишите свою.`
              : planId ? 'Помогу выстроить историю и уточнить содержание плана.' : 'Помогу с содержанием, цифрами и слайдами.'}</span>
            <div className={styles.suggestions}>
              {suggestions.map((suggestion) => (
                <button type="button" key={suggestion} disabled={busy} onClick={() => void submit(suggestion)}>
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}
        {items.map((message) => (
          <article key={message.id} className={styles.message} data-role={message.role}>
            <span className={styles.author}>{message.role === 'user' ? 'Вы' : 'Помощник'}</span>
            <p className={styles.messageText}>{message.text}</p>
            {message.actions && message.actions.length > 0 && <AgentActions actions={message.actions} />}
            {message.question && (
              <div className={styles.question}>
                <p>{message.question.text}</p>
                <div className={styles.options} role="group" aria-label="Варианты ответа">
                  {message.question.options.map((option) => (
                    <button
                      key={option}
                      type="button"
                      disabled={busy || clear.isPending}
                      onClick={() => void submit(option)}
                    >
                      {option}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {message.mode === 'unavailable' && (
              <p className={styles.notice}>
                Без подключённого провайдера я понимаю только короткие команды.{' '}
                <Link to={`?${providerParams}`}>Подключить провайдера</Link>
              </p>
            )}
            {message.mode === 'error' && (
              <Button
                size="sm"
                variant="secondary"
                disabled={busy}
                onClick={() => {
                  const original =
                    items.find((item) => item.id === message.reply_to) ??
                    items.slice(0, items.indexOf(message)).findLast((item) => item.role === 'user')
                  if (original) void submit(original.text)
                }}
              >
                Повторить
              </Button>
            )}
          </article>
        ))}
        {jobId && (
          <div className={styles.live}>
            {run.steps.length > 0 && <AgentActions actions={run.steps} />}
            <p className={styles.status} role="status" aria-live="polite">
              {run.status ?? (run.done ? 'Обновляю презентацию…' : 'Думаю…')}
            </p>
            {run.error && (
              <p className={styles.notice}>
                Связь прервалась.{' '}
                <button type="button" onClick={() => void run.retry()}>
                  Подключиться снова
                </button>
              </p>
            )}
          </div>
        )}
        <div ref={bottom} />
      </div>
      <div className={styles.composer}>
        {(error || thread.error) && (
          <p className={styles.error} role="alert">
            {error ?? humanError(thread.error)}
            {thread.error && (
              <button type="button" onClick={() => void thread.refetch()}>
                Повторить
              </button>
            )}
          </p>
        )}
        {(canUndo || variantId || planId || currentSlideId) && (
          <div className={styles.composerMeta}>
            {(variantId || planId || currentSlideId) && (
              <div className={styles.context}>
                <span>
                  {currentSlideId ? `${slideNumber !== null ? `Слайд ${slideNumber}` : 'Открытый слайд'} · ` : ''}
                  {variantId ? 'Доводка' : 'План'}
                </span>
                {currentSlideId && (
                  <button
                    type="button"
                    aria-label="Снять привязку к слайду"
                    onClick={() => setContext({ slideId, detached: true })}
                  >
                    <X size={13} />
                  </button>
                )}
              </div>
            )}
            {canUndo && (
              <button
                className={styles.undo}
                type="button"
                disabled={busy || clear.isPending}
                onClick={() => void submit('Отмени последнюю правку')}
              >
                <RotateCcw size={13} />
                Отменить последнюю правку
              </button>
            )}
          </div>
        )}
        <form
          className={styles.composerBox}
          onSubmit={(event) => {
            event.preventDefault()
            void submit(text)
          }}
        >
          <textarea
            ref={input}
            className={styles.input}
            aria-label="Сообщение помощнику"
            placeholder={currentSlideId ? 'Что изменить на этом слайде?' : 'Напишите, что поменять или спросите совета'}
            rows={2}
            value={text}
            disabled={busy || clear.isPending || thread.isPending}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault()
                void submit(text)
              }
            }}
          />
          <div className={styles.sendRow}>
            {busy ? (
              <>
                <span>Ещё отвечаю…</span>
                <Button size="sm" variant="secondary" disabled={!jobId || cancel.isPending} onClick={() => void stop()}>
                  <Square size={13} />
                  Остановить
                </Button>
              </>
            ) : (
              <>
                <span>Enter — отправить</span>
                <Button
                  type="submit"
                  size="sm"
                  variant="primary"
                  disabled={!text.trim() || clear.isPending || thread.isPending || Boolean(thread.error)}
                >
                  <Send size={13} />
                  Отправить
                </Button>
              </>
            )}
          </div>
        </form>
      </div>
    </section>
  )
}
