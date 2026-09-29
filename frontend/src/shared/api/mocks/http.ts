import { delay, http, HttpResponse } from 'msw'
import type { RequestHandler } from 'msw'
import { API_BASE } from '@/shared/config'
import { randomHex } from './ids'

export type DelayRange = number | readonly [number, number]

interface ErrorOptions {
  stage?: string | null
  retryable?: boolean
  details?: Record<string, unknown>
}

export class MockApiError extends Error {
  readonly status: number
  readonly code: string
  readonly stage: string | null
  readonly retryable: boolean
  readonly details: Record<string, unknown>

  constructor(status: number, code: string, message: string, options: ErrorOptions = {}) {
    super(message)
    this.name = 'MockApiError'
    this.status = status
    this.code = code
    this.stage = options.stage ?? null
    this.retryable = options.retryable ?? false
    this.details = options.details ?? {}
  }
}

export function notFound(entity: string, id: string): MockApiError {
  return new MockApiError(404, 'not_found', `${entity} '${id}' not found`, { details: { entity, id } })
}

export function validationError(field: string, message: string, location: 'body' | 'query' = 'body'): MockApiError {
  return new MockApiError(422, 'validation_error', message, {
    stage: 'request',
    details: { errors: [{ loc: [location, field], msg: message, type: 'value_error' }] },
  })
}

export function stateConflict(message: string, details: Record<string, unknown> = {}): MockApiError {
  return new MockApiError(409, 'state_conflict', message, { details })
}

export function errorResponse(error: MockApiError): Response {
  const requestId = `req_${randomHex(16)}`
  return HttpResponse.json(
    {
      error: {
        code: error.code,
        message: error.message,
        stage: error.stage,
        retryable: error.retryable,
        request_id: requestId,
        details: error.details,
      },
    },
    { status: error.status, headers: { 'X-Request-ID': requestId } },
  )
}

export function json(body: unknown, status = 200): Response {
  return HttpResponse.json(body as Record<string, unknown>, { status })
}

export function noContent(): Response {
  return new HttpResponse(null, { status: 204 })
}

function pick(range: DelayRange): number {
  if (typeof range === 'number') return range
  const [min, max] = range
  return Math.round(min + Math.random() * (max - min))
}

export async function wait(range: DelayRange): Promise<void> {
  const milliseconds = pick(range)
  if (milliseconds > 0) await delay(milliseconds)
}

export interface RouteContext {
  request: Request
  url: URL
  param: (name: string) => string
}

type Method = 'get' | 'post' | 'patch' | 'delete'
type Resolver = (context: RouteContext) => Response | Promise<Response>

export interface Router {
  route: (method: Method, path: string, resolver: Resolver) => RequestHandler
  fallback: () => RequestHandler
}

function paramReader(params: Record<string, string | readonly string[] | undefined>): (name: string) => string {
  return (name) => {
    const value = params[name]
    const raw = typeof value === 'string' ? value : value?.[0]
    if (raw === undefined) throw new MockApiError(400, 'bad_request', `Missing path parameter '${name}'`)
    return raw
  }
}

async function settle(resolver: () => Response | Promise<Response>): Promise<Response> {
  try {
    return await resolver()
  } catch (error) {
    if (error instanceof MockApiError) return errorResponse(error)
    const message = error instanceof Error ? error.message : 'Unexpected mock backend failure'
    return errorResponse(new MockApiError(500, 'internal_error', message, { retryable: true }))
  }
}

export function createRouter(options: { latency: DelayRange; ready: () => Promise<void> }): Router {
  const pattern = (path: string) => `*${API_BASE}${path}`
  return {
    route: (method, path, resolver) =>
      http[method](pattern(path), async ({ request, params }) => {
        await wait(options.latency)
        return settle(async () => {
          await options.ready()
          return resolver({ request, url: new URL(request.url), param: paramReader(params) })
        })
      }),
    fallback: () =>
      http.all(pattern('/*'), ({ request }) => {
        const url = new URL(request.url)
        return errorResponse(new MockApiError(404, 'not_found', `No route for ${request.method} ${url.pathname}`))
      }),
  }
}
