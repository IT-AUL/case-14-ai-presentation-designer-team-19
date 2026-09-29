import { stageLabel, type GenerationTracker } from '@/entities/generation'
import { isApiError } from '@/shared/api'

export interface FailureView {
  code: string | null
  message: string
  stage: string | null
  retryable: boolean | null
}

const FALLBACK_MESSAGE = 'Сервис сообщил об ошибке без подробностей.'

export function describeFailure(tracker: Pick<GenerationTracker, 'error' | 'jobError' | 'job'>): FailureView {
  const { jobError, error, job } = tracker
  if (jobError) {
    return { code: jobError.code, message: jobError.message || FALLBACK_MESSAGE, stage: stageLabel(jobError.stage ?? job?.stage), retryable: null }
  }
  if (isApiError(error)) {
    return { code: error.code, message: error.message || FALLBACK_MESSAGE, stage: stageLabel(error.stage), retryable: error.retryable }
  }
  if (error) return { code: null, message: error.message || FALLBACK_MESSAGE, stage: null, retryable: null }
  return { code: null, message: FALLBACK_MESSAGE, stage: stageLabel(job?.stage), retryable: null }
}

export function failureMeta({ code, stage, retryable }: FailureView): string | null {
  const parts = [code?.toUpperCase(), stage ? `этап: ${stage.toLowerCase()}` : null, retryable === null ? null : retryable ? 'можно повторить' : 'повтор не поможет']
  const meta = parts.filter(Boolean).join(' · ')
  return meta || null
}

export function isTrackingLost(error: Error | null): boolean {
  return isApiError(error) && error.code === 'tracking_lost'
}
