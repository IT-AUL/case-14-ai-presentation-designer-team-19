import type { components } from './schema'

type ErrorEnvelope = components['schemas']['ErrorEnvelope']

export class ApiError extends Error {
  readonly code: string
  readonly status: number
  readonly stage: string | null
  readonly retryable: boolean
  readonly requestId: string | null

  constructor(params: { code: string; message: string; status: number; stage?: string | null; retryable?: boolean; requestId?: string | null }) {
    super(params.message)
    this.name = 'ApiError'
    this.code = params.code
    this.status = params.status
    this.stage = params.stage ?? null
    this.retryable = params.retryable ?? false
    this.requestId = params.requestId ?? null
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
      })
    }
    return new ApiError({ code: status >= 500 ? 'internal_error' : 'unknown_error', message: `HTTP ${status}`, status, retryable: status >= 500 })
  }
}

export function isApiError(error: unknown): error is ApiError {
  return error instanceof ApiError
}
