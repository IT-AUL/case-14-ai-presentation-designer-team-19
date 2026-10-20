import { HttpResponse } from 'msw'
import type { RequestHandler } from 'msw'
import { page } from '../context'
import type { MockContext } from '../context'
import { assertRetryable, cancelGeneration, completeGeneration, createPlanSet, generationDetail, parseGenerationRequest, startGeneration } from '../generation'
import { json, MockApiError, wait } from '../http'
import type { RunRecord } from '../store'
import type { Job } from '../types'
import { queryInteger, readJson } from '../validate'

function accepted(run: RunRecord): Response {
  return json({ generation_id: run.detail.id, job_id: run.detail.job_id, variant_ids: run.variantIds }, 202)
}

function jobEvents(job: Job, after: number): string {
  const timeline = [
    { state: 'queued', stage: null, progress: 0 },
    { state: 'running', stage: job.stage ?? 'running', progress: job.state === 'running' ? job.progress : 0.5 },
    ...(job.state === 'running' || job.state === 'queued' ? [] : [{ state: job.state, stage: job.stage ?? null, progress: job.progress }]),
  ]
  return timeline
    .map((event, index) => ({ id: index + 1, ...event }))
    .filter((event) => event.id > after)
    .map((event) => `id: ${event.id}\nevent: job\ndata: ${JSON.stringify({ job_id: job.id, ...event })}\n\n`)
    .join('')
}

export function generationRoutes(context: MockContext): RequestHandler[] {
  const { store, router, generationDelay } = context
  const { route } = router

  const run = async (projectId: string, request: RunRecord['request'], parentRunId: string | null) => {
    const record = startGeneration(store, projectId, request, parentRunId)
    store.touchProject(projectId, { latest_run_id: record.detail.id, latest_run_state: 'running' })
    await wait(generationDelay)
    await completeGeneration(store, record.detail.id)
    store.touchProject(projectId, { latest_run_id: record.detail.id, latest_run_state: 'completed' })
    return record
  }

  return [
    route('post', '/projects/:projectId/generations', async ({ request, param }) => {
      const projectId = param('projectId')
      const input = parseGenerationRequest(store, projectId, await readJson(request))
      return accepted(await run(projectId, input, null))
    }),

    route('post', '/projects/:projectId/plans', async ({ request, param }) => json(createPlanSet(store, param('projectId'), await readJson(request)))),

    route('get', '/generations/:runId', ({ param }) => json(generationDetail(store, store.run(param('runId'))))),

    route('post', '/generations/:runId/cancel', ({ param }) => {
      const job = cancelGeneration(store, param('runId'))
      const record = store.run(param('runId'))
      store.touchProject(record.detail.project_id, { latest_run_id: record.detail.id, latest_run_state: 'canceled' })
      return json(job)
    }),

    route('post', '/generations/:runId/retry', async ({ param }) => {
      const previous = assertRetryable(store, param('runId'))
      return accepted(await run(previous.detail.project_id, previous.request, previous.detail.id))
    }),

    route('get', '/generations/:runId/variants', ({ param }) => {
      const record = store.run(param('runId'))
      return json(page(record.variantIds.map((id) => store.variant(id).summary)))
    }),

    route('get', '/variants/:variantId', ({ param }) => json(store.variant(param('variantId')).summary)),

    route('get', '/variants/:variantId/slides', ({ param, url }) => {
      const limit = queryInteger(url, 'limit', { min: 1, max: 500, fallback: 100 })
      return json(page(store.variant(param('variantId')).slides.slice(0, limit)))
    }),

    route('get', '/variants/:variantId/slide-drafts', ({ param }) => {
      const variantId = param('variantId')
      const record = store.variant(variantId)
      const slides = record.slides.map((slide, idx) => {
        const index = typeof slide.index === 'number' ? slide.index : idx + 1
        return {
          variant_id: variantId,
          index,
          slide_id: slide.slide_plan_id ?? `slide-${idx}`,
          slide_key: `${variantId}:${index}`,
          revision: record.revision,
          preview_revision: record.summary.status === 'completed' ? 2 : 1,
          preview_artifact_id: slide.preview_artifact_id ?? null,
          status: record.summary.status === 'completed' ? 'rendered' : 'draft',
        }
      })
      return json({ variant_id: variantId, state: record.summary.status, slides })
    }),

    route('get', '/slides/:slideId', ({ param }) => json(store.slide(param('slideId')))),

    route('get', '/slides/:slideId/preview', ({ param }) => {
      const slide = store.slide(param('slideId'))
      throw new MockApiError(404, 'not_found', `Preview for slide ${slide.id} has not been rendered`, { stage: 'render', details: { slide_id: slide.id } })
    }),

    route('get', '/jobs/:jobId', ({ param }) => json(store.job(param('jobId')))),

    route('get', '/jobs/:jobId/events', ({ param, url }) => {
      const job = store.job(param('jobId'))
      const after = queryInteger(url, 'after', { min: 0, max: Number.MAX_SAFE_INTEGER, fallback: 0 })
      return new HttpResponse(jobEvents(job, after), {
        headers: { 'Content-Type': 'text/event-stream; charset=utf-8', 'Cache-Control': 'no-cache' },
      })
    }),
  ]
}
