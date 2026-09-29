import type { RequestHandler } from 'msw'
import { page } from '../context'
import type { MockContext } from '../context'
import { json, noContent, validationError } from '../http'
import { nowIso, prefixedId } from '../ids'
import type { Project } from '../types'
import { optionalInteger, optionalString, queryInteger, readJson, requiredString } from '../validate'
import type { JsonObject } from '../validate'

const SLIDE_COUNT = { min: 3, max: 40 }

function languageOf(body: JsonObject, fallback: string): string {
  const language = optionalString(body, 'default_language') ?? fallback
  if (language.trim() === '' || language.length > 16) throw validationError('default_language', "Field 'default_language' must be a short language code")
  return language
}

export function projectRoutes({ store, router }: MockContext): RequestHandler[] {
  const { route } = router
  return [
    route('get', '/projects', ({ url }) => {
      const limit = queryInteger(url, 'limit', { min: 1, max: 500, fallback: 50 })
      const items = [...store.projects.values()].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, limit)
      return json(page(items))
    }),

    route('post', '/projects', async ({ request }) => {
      const body = await readJson(request)
      const createdAt = nowIso()
      const project: Project = {
        id: prefixedId('prj'),
        name: requiredString(body, 'name', 200).trim(),
        status: 'draft',
        default_language: languageOf(body, 'ru'),
        target_slide_count: optionalInteger(body, 'target_slide_count', { ...SLIDE_COUNT, fallback: 12 }),
        template_id: null,
        content_pack_id: null,
        created_at: createdAt,
        updated_at: createdAt,
      }
      store.projects.set(project.id, project)
      return json(project, 201)
    }),

    route('get', '/projects/:projectId', ({ param }) => json(store.project(param('projectId')))),

    route('patch', '/projects/:projectId', async ({ request, param }) => {
      const current = store.project(param('projectId'))
      const body = await readJson(request)
      const name = body.name === undefined || body.name === null ? current.name : requiredString(body, 'name', 200).trim()
      const updated: Project = {
        ...current,
        name,
        default_language: languageOf(body, current.default_language),
        target_slide_count: optionalInteger(body, 'target_slide_count', { ...SLIDE_COUNT, fallback: current.target_slide_count }),
        updated_at: nowIso(),
      }
      store.projects.set(updated.id, updated)
      return json(updated)
    }),

    route('delete', '/projects/:projectId', ({ param }) => {
      const project = store.project(param('projectId'))
      store.projects.delete(project.id)
      return noContent()
    }),
  ]
}
