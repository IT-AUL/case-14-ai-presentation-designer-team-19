import type { RequestHandler } from 'msw'
import type { MockContext } from '../context'
import { json, noContent, validationError } from '../http'
import { isoAfter, nowIso, prefixedId } from '../ids'
import type { ProviderCapabilities, ProviderSession } from '../types'
import { isRecord, optionalBoolean, optionalInteger, optionalString, readJson, requiredString } from '../validate'
import type { JsonObject } from '../validate'

const CAPABILITIES: readonly (keyof ProviderCapabilities)[] = ['structured_output', 'tool_calls', 'image_input', 'embeddings']

function parseBaseUrl(body: JsonObject): string {
  const raw = requiredString(body, 'base_url', 2000).trim()
  const url = URL.canParse(raw) ? new URL(raw) : null
  if (!url || (url.protocol !== 'http:' && url.protocol !== 'https:')) {
    throw validationError('base_url', "Field 'base_url' must be an absolute http(s) URL")
  }
  return raw.replace(/\/+$/, '')
}

function parseModels(value: unknown): ProviderSession['models'] {
  if (!isRecord(value)) throw validationError('models', "Field 'models' is required")
  return {
    text: requiredString(value, 'text', 200),
    vision: requiredString(value, 'vision', 200),
    embedding: optionalString(value, 'embedding'),
    image: optionalString(value, 'image'),
  }
}

function parseCapabilities(value: unknown): ProviderCapabilities {
  const source = isRecord(value) ? value : {}
  return {
    structured_output: optionalBoolean(source, 'structured_output', false),
    tool_calls: optionalBoolean(source, 'tool_calls', false),
    image_input: optionalBoolean(source, 'image_input', false),
    embeddings: optionalBoolean(source, 'embeddings', false),
  }
}

export function providerSessionRoutes({ store, router }: MockContext): RequestHandler[] {
  const { route } = router
  return [
    route('post', '/provider-sessions', async ({ request }) => {
      const body = await readJson(request)
      const label = requiredString(body, 'label', 200)
      const baseUrl = parseBaseUrl(body)
      requiredString(body, 'api_token', 4096)
      const models = parseModels(body.models)
      const capabilities = parseCapabilities(body.capabilities)
      optionalInteger(body, 'timeout_seconds', { min: 1, max: 600, fallback: 90 })
      optionalInteger(body, 'max_concurrency', { min: 1, max: 64, fallback: 4 })
      const ttl = optionalInteger(body, 'ttl_seconds', { min: 60, max: 86400, fallback: 14400 })
      const projectId = optionalString(body, 'project_id')
      if (projectId) store.project(projectId)
      const createdAt = nowIso()
      const session: ProviderSession = {
        id: prefixedId('ps'),
        label,
        base_url: baseUrl,
        models,
        capabilities,
        project_id: projectId,
        created_at: createdAt,
        expires_at: isoAfter(createdAt, ttl * 1000),
      }
      store.sessions.set(session.id, session)
      return json(session, 201)
    }),

    route('post', '/provider-sessions/:sessionId/test', ({ param }) => {
      const session = store.session(param('sessionId'))
      const unreachable = new URL(session.base_url).hostname.endsWith('.invalid')
      const results = CAPABILITIES.map((capability) => {
        if (capability === 'tool_calls') return { capability, status: 'skip', detail: 'not_probeable' }
        if (!session.capabilities[capability]) return { capability, status: 'skip', detail: 'not_declared' }
        return unreachable ? { capability, status: 'fail', detail: 'connection_error' } : { capability, status: 'ok', detail: null }
      })
      return json({ session_id: session.id, results, tested_at: nowIso() })
    }),

    route('delete', '/provider-sessions/:sessionId', ({ param }) => {
      const session = store.session(param('sessionId'))
      store.sessions.delete(session.id)
      return noContent()
    }),
  ]
}
