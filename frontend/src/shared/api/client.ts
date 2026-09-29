import createClient from 'openapi-fetch'
import { ApiError } from './errors'
import type { paths } from './schema'

export const api = createClient<paths>({ baseUrl: '' })

interface FetchResult<T> {
  data?: T
  error?: unknown
  response: Response
}

export async function unwrap<T>(request: Promise<FetchResult<T>>): Promise<T> {
  const { data, error, response } = await request
  if (!response.ok) throw ApiError.fromResponse(response.status, error)
  return data as T
}
