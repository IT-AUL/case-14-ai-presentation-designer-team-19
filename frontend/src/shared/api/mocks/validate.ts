import { MockApiError, validationError } from './http'
import type { UploadedFile } from './types'

export type JsonObject = Record<string, unknown>

export function isRecord(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export async function readJson(request: Request): Promise<JsonObject> {
  const text = await request.text()
  if (text.trim() === '') return {}
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw validationError('body', 'Request body is not valid JSON')
  }
  if (!isRecord(parsed)) throw validationError('body', 'Request body must be a JSON object')
  return parsed
}

export function requiredString(body: JsonObject, field: string, maxLength = 2000): string {
  const value = body[field]
  if (typeof value !== 'string' || value.trim() === '') throw validationError(field, `Field '${field}' is required`)
  if (value.length > maxLength) throw validationError(field, `Field '${field}' must be at most ${maxLength} characters`)
  return value
}

export function optionalString(body: JsonObject, field: string): string | null {
  const value = body[field]
  if (value === undefined || value === null) return null
  if (typeof value !== 'string') throw validationError(field, `Field '${field}' must be a string`)
  return value
}

export function optionalInteger(body: JsonObject, field: string, bounds: { min: number; max: number; fallback: number }): number {
  const value = body[field]
  if (value === undefined || value === null) return bounds.fallback
  if (typeof value !== 'number' || !Number.isInteger(value) || value < bounds.min || value > bounds.max) {
    throw validationError(field, `Field '${field}' must be an integer between ${bounds.min} and ${bounds.max}`)
  }
  return value
}

export function optionalBoolean(body: JsonObject, field: string, fallback: boolean): boolean {
  const value = body[field]
  if (value === undefined || value === null) return fallback
  if (typeof value !== 'boolean') throw validationError(field, `Field '${field}' must be a boolean`)
  return value
}

export function stringArray(body: JsonObject, field: string, bounds: { min: number; max: number }): string[] {
  const value = body[field]
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw validationError(field, `Field '${field}' must be an array of strings`)
  }
  if (value.length < bounds.min || value.length > bounds.max) {
    throw validationError(field, `Field '${field}' must contain between ${bounds.min} and ${bounds.max} items`)
  }
  return value as string[]
}

export function queryInteger(url: URL, field: string, bounds: { min: number; max: number; fallback: number }): number {
  const raw = url.searchParams.get(field)
  if (raw === null || raw === '') return bounds.fallback
  const value = Number(raw)
  if (!Number.isInteger(value) || value < bounds.min || value > bounds.max) {
    throw validationError(field, `Query parameter '${field}' must be an integer between ${bounds.min} and ${bounds.max}`, 'query')
  }
  return value
}

export function queryBoolean(url: URL, field: string): boolean | null {
  const raw = url.searchParams.get(field)
  if (raw === null || raw === '') return null
  const normalized = raw.toLowerCase()
  if (['true', '1', 'yes'].includes(normalized)) return true
  if (['false', '0', 'no'].includes(normalized)) return false
  throw validationError(field, `Query parameter '${field}' must be a boolean`, 'query')
}

export function queryList(url: URL, field: string): string[] | null {
  const values = url.searchParams
    .getAll(field)
    .flatMap((value) => value.split(','))
    .map((value) => value.trim())
    .filter(Boolean)
  return values.length > 0 ? values : null
}

export async function readForm(request: Request): Promise<FormData> {
  const contentType = request.headers.get('content-type') ?? ''
  if (!contentType.includes('multipart/form-data')) {
    throw new MockApiError(415, 'unsupported_media_type', 'Expected a multipart/form-data request', { stage: 'request' })
  }
  try {
    return await request.formData()
  } catch {
    throw validationError('body', 'Malformed multipart body')
  }
}

export async function formFiles(form: FormData, field: string): Promise<UploadedFile[]> {
  const entries = form.getAll(field).filter((entry): entry is File => typeof entry !== 'string')
  return Promise.all(
    entries.map(async (entry) => ({ name: entry.name, type: entry.type, bytes: new Uint8Array(await entry.arrayBuffer()) })),
  )
}
