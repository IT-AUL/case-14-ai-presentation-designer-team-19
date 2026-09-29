import { API_BASE } from '@/shared/config'
import { ApiError } from './errors'

const UNSUPPORTED_STATUSES = new Set([404, 405, 501])

interface RawRequest {
  method?: 'GET' | 'POST'
  body?: unknown
  query?: Record<string, string | number | boolean | null | undefined>
  signal?: AbortSignal
}

function buildUrl(path: string, query: RawRequest['query']): string {
  const url = `${API_BASE}${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) if (value !== null && value !== undefined) params.set(key, String(value))
  const search = params.toString()
  return search ? `${url}?${search}` : url
}

function isJson(response: Response): boolean {
  return (response.headers.get('content-type') ?? '').toLowerCase().includes('json')
}

async function readBody(response: Response): Promise<unknown> {
  if (!isJson(response)) return undefined
  try {
    return await response.json()
  } catch {
    return undefined
  }
}

async function send(path: string, request: RawRequest): Promise<Response> {
  const hasBody = request.body !== undefined
  return fetch(buildUrl(path, request.query), {
    method: request.method ?? (hasBody ? 'POST' : 'GET'),
    headers: hasBody ? { 'content-type': 'application/json', accept: 'application/json' } : { accept: 'application/json' },
    body: hasBody ? JSON.stringify(request.body) : undefined,
    signal: request.signal,
  })
}

export function isUnsupportedEndpoint(error: unknown): boolean {
  return error instanceof ApiError && UNSUPPORTED_STATUSES.has(error.status)
}

export async function requestJson<T>(path: string, request: RawRequest = {}): Promise<T> {
  const response = await send(path, request)
  const body = await readBody(response)
  if (!response.ok) throw ApiError.fromResponse(response.status, body)
  if (body === undefined) throw new ApiError({ code: 'unsupported_response', message: `Ответ ${path} не в формате JSON`, status: 501 })
  return body as T
}

export async function requestOptionalJson<T>(path: string, request: RawRequest = {}): Promise<T | null> {
  try {
    return await requestJson<T>(path, request)
  } catch (error) {
    if (isUnsupportedEndpoint(error)) return null
    throw error
  }
}
