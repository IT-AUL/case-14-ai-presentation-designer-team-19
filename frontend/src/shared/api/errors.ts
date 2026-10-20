import type { components } from './schema'

type ErrorEnvelope = components['schemas']['ErrorEnvelope']

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly stage: string | null
  readonly retryable: boolean
  readonly requestId: string | null
  readonly details: Record<string, unknown>

  constructor(params: { code: string; message: string; status: number; stage?: string | null; retryable?: boolean; requestId?: string | null; details?: Record<string, unknown> }) {
    super(params.message)
    this.name = 'ApiError'
    this.code = params.code
    this.status = params.status
    this.stage = params.stage ?? null
    this.retryable = params.retryable ?? false
    this.requestId = params.requestId ?? null
    this.details = params.details ?? {}
  }

  static fromResponse(status: number, body: unknown): ApiError {
    const envelope = body as Partial<ErrorEnvelope> | undefined
    const error = envelope?.error
    if (error) {
      return new ApiError({
        code: error.code,
        message: error.message,
        status,
        stage: error.stage,
        retryable: error.retryable,
        requestId: error.request_id,
        details: error.details ?? {},
      })
    }
    return new ApiError({ code: status >= 500 ? 'internal_error' : 'unknown_error', message: `HTTP ${status}`, status, retryable: status >= 500 })
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}

const NETWORK_FAILURE = /failed to fetch|networkerror|load failed|network request failed/i
const LATIN_ONLY = /^[\x20-\x7e]+$/
const QUOTED_PART = /"[^"]*"|'[^']*'|«[^»]*»|`[^`]*`/g

export function humanError(error: unknown, fallback = 'Что-то пошло не так. Попробуйте ещё раз.'): string {
  if (!(error instanceof Error)) return fallback
  const message = error.message.trim()
  if (!message || NETWORK_FAILURE.test(message)) return 'Нет связи с сервером. Проверьте соединение и попробуйте ещё раз.'
  if (isApiError(error) && error.status === 404) return 'Не нашёл эти данные. Обновите страницу или вернитесь на шаг назад.'
  if (isApiError(error) && error.status >= 500) return 'Сервер не справился. Попробуйте ещё раз через минуту.'
  const unquoted = message.replace(QUOTED_PART, '').trim()
  if (/^HTTP \d+$/.test(message) || LATIN_ONLY.test(message) || !unquoted || LATIN_ONLY.test(unquoted)) return fallback
  return message
}
