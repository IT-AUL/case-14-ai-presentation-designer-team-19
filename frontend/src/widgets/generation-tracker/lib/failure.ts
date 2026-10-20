import { stageLabel, type GenerationTracker } from '@/entities/generation'
import { isApiError } from '@/shared/api'

export interface FailureView {
  code: string | null
  message: string
  stage: string | null
  retryable: boolean | null
  technical: string | null
}

const FALLBACK_MESSAGE = 'Сервис сообщил об ошибке без подробностей.'
const HUMAN_MESSAGE = 'Не получилось собрать колоду. Материалы и план сохранены — попробуйте ещё раз или выберите другой стиль.'
const PROVIDER_MESSAGE = 'Внешний сервис проверки временно не ответил. Черновики слайдов сохранены — нажмите «Повторить», чтобы завершить сборку.'
const INTERRUPTED_MESSAGE = 'Сервер перезапустился во время сборки. Материалы и план сохранены — нажмите «Повторить».'
const TECHNICAL = /^[A-Za-z_.]*(Error|Exception)\b|Traceback|NoneType|object has no attribute|^provider\s+HTTP\s+\d+|^Server restarted/i

function humanize(code: string | null, raw: string | null | undefined, stage: string | null, retryable: boolean | null): FailureView {
  const message = raw?.trim() || FALLBACK_MESSAGE
  const technical = code === 'internal_error' || TECHNICAL.test(message)
  const readable =
    !technical
      ? message
      : code === 'provider_unavailable'
        ? PROVIDER_MESSAGE
        : code === 'execution_interrupted'
          ? INTERRUPTED_MESSAGE
          : HUMAN_MESSAGE
  return { code, message: readable, stage, retryable, technical: technical ? message : null }
}

export function describeFailure(tracker: Pick<GenerationTracker, 'error' | 'jobError' | 'job'>): FailureView {
  const { jobError, error, job } = tracker
  if (jobError) {
    return humanize(jobError.code, jobError.message, stageLabel(jobError.stage ?? job?.stage), null)
  }
  if (isApiError(error)) {
    return humanize(error.code, error.message, stageLabel(error.stage), error.retryable)
  }
  if (error) return humanize(null, error.message, null, null)
  return humanize(null, null, stageLabel(job?.stage), null)
}

export function failureMeta({ code, stage, retryable }: FailureView): string | null {
  const parts = [code?.toUpperCase(), stage ? `этап: ${stage.toLowerCase()}` : null, retryable === null ? null : retryable ? 'можно повторить' : 'повтор не поможет']
  const meta = parts.filter(Boolean).join(' · ')
  return meta || null
}

export function isTrackingLost(error: Error | null): boolean {
  return isApiError(error) && error.code === 'tracking_lost'
}
