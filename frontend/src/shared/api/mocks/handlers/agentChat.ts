import { http, HttpResponse, delay } from 'msw'
import { API_BASE } from '@/shared/config'
import type { components } from '../../schema'
import type { MockContext } from '../context'
import { json, noContent, notFound, stateConflict, validationError } from '../http'
import { nowIso, prefixedId } from '../ids'
import { readJson } from '../validate'

type Message = components['schemas']['AgentMessage']
type Thread = components['schemas']['AgentThread']
type Body = components['schemas']['AgentMessageRequest']
type Action = components['schemas']['AgentAction']
type Object = Record<string, unknown>
interface Deck {
  summary: Object
  slides: Object[]
}
interface Frame {
  type: string
  data: Object
}
interface Run {
  id: string
  projectId: string
  body: Body
  frames: Frame[]
  events: Frame[]
  applied: boolean
  canceled: boolean
  startedAt: number
}
interface Snapshot {
  slides: Object[]
  summary: Object
}
const storageKey = 'deckdna.mock.agent.v1'

export function agentChatHandlers(
  { router, store }: Pick<MockContext, 'router' | 'store'>,
  resolve: { deck: (id: string) => Deck | null; plan: (id: string) => Object; task: (id: string) => Object },
) {
  const threads = new Map<string, Thread>()
  const runs = new Map<string, Run>()
  const snapshots = new Map<string, Snapshot>()
  const previews = new Map<string, number>()
  const savedDecks = new Map<string, Deck>()
  const loadedDecks = new Map<string, Deck>()
  try {
    const saved = JSON.parse(sessionStorage.getItem(storageKey) ?? 'null') as {
      threads: [string, Thread][]
      runs: [string, Run][]
      decks?: [string, Deck][]
      previews?: [string, number][]
      snapshots?: [string, Snapshot][]
    } | null
    saved?.threads.forEach(([id, thread]) => threads.set(id, thread))
    saved?.runs.forEach(([id, run]) => runs.set(id, run))
    saved?.decks?.forEach(([id, deck]) => savedDecks.set(id, deck))
    saved?.previews?.forEach(([id, revision]) => previews.set(id, revision))
    saved?.snapshots?.forEach(([id, snapshot]) => snapshots.set(id, snapshot))
  } catch {
    threads.clear()
    runs.clear()
  }
  const save = () => {
    try {
      sessionStorage.setItem(
        storageKey,
        JSON.stringify({
          threads: [...threads],
          runs: [...runs],
          decks: [...new Map([...savedDecks, ...loadedDecks])],
          previews: [...previews],
          snapshots: [...snapshots],
        }),
      )
    } catch {
      return
    }
  }
  const thread = (id: string): Thread => {
    let current = threads.get(id)
    if (!current) {
      current = { items: [], active_job_id: null }
      threads.set(id, current)
    }
    return current
  }
  const deckFor = (id: string) => {
    const deck = resolve.deck(id)
    if (!deck) return null
    if (!loadedDecks.has(id)) {
      const saved = savedDecks.get(id)
      if (saved) {
        deck.slides.splice(0, deck.slides.length, ...structuredClone(saved.slides))
        Object.assign(deck.summary, saved.summary)
      }
      loadedDecks.set(id, deck)
    }
    return deck
  }
  const apply = (run: Run) => {
    if (run.applied) return
    run.applied = true
    const body = run.body
    if (body.variant_id) {
      const deck = deckFor(body.variant_id)
      if (!deck) return
      const undo = /отмен[аи]/i.test(body.text)
      if (undo) {
        const previous = snapshots.get(run.projectId)
        if (previous) {
          deck.slides.splice(0, deck.slides.length, ...structuredClone(previous.slides))
          Object.assign(deck.summary, previous.summary)
          snapshots.delete(run.projectId)
          const revision = Number(run.frames.find((frame) => frame.type === 'deck.revision')?.data.revision ?? 1)
          const restored =
            deck.slides.find((item) => item.id === body.slide_id || item.slide_plan_id === body.slide_id) ??
            deck.slides[0]
          if (restored) restored.revision = revision
        }
      } else {
        snapshots.set(run.projectId, structuredClone(deck))
        const slide =
          deck.slides.find((item) => item.id === body.slide_id || item.slide_plan_id === body.slide_id) ??
          deck.slides[0]
        if (slide) {
          const action = run.frames.find((frame) => frame.type === 'slide.updated')?.data
          if (action) {
            Object.assign(slide, {
              revision: action.revision,
              preview_artifact_id: action.preview_artifact_id,
              title: 'Главное — рост и результат',
            })
            previews.set(String(action.preview_artifact_id), Number(action.revision))
          }
        }
      }
      const record = store.variants.get(body.variant_id)
      if (record) record.revision = Math.max(...deck.slides.map((item) => Number(item.revision ?? 1)))
    } else if (body.plan_id) {
      const plan = resolve.plan(body.plan_id)
      for (const entry of (plan.plans ?? []) as Object[]) {
        const deck = entry.deck_plan as { slides?: Object[] } | undefined
        const slide = deck?.slides?.find((item) => item.id === body.slide_id) ?? deck?.slides?.[0]
        if (slide) {
          slide.title_intent = 'Главное — рост и результат'
          slide.content_units = [
            { role: 'bullet', text: 'Сократил до главного вывода', origin: 'user', evidence_ids: [] },
          ]
        }
      }
    } else {
      const task = resolve.task(run.projectId)
      task.brief = { ...((task.brief as Object) ?? {}), key_message: body.text }
    }
  }
  const emit = (run: Run, frame: Frame) => {
    if (frame.type === 'agent.action' && frame.data.phase === 'finished' && frame.data.tool !== 'web_research')
      apply(run)
    run.events.push(frame)
    if (frame.type === 'agent.message') thread(run.projectId).items.push(frame.data.message as Message)
    if (frame.type === 'job.completed' || frame.type === 'job.failed') thread(run.projectId).active_job_id = null
    save()
  }
  const schedule = (run: Run) => {
    for (let index = run.events.length; index < run.frames.length; index++) {
      const frame = run.frames[index]
      if (!frame) continue
      const timer = setTimeout(
        () => {
          if (!run.canceled && run.events.length === index) emit(run, frame)
        },
        Math.max(0, run.startedAt + (index + 1) * 220 - Date.now()),
      )
      ;(timer as { unref?: () => void }).unref?.()
    }
  }
  for (const run of runs.values()) if (thread(run.projectId).active_job_id === run.id) schedule(run)
  const { route } = router
  return [
    route('get', '/variants/:variantId', ({ param }) => {
      const deck = deckFor(param('variantId'))
      if (!deck) throw notFound('Variant', param('variantId'))
      return json(deck.summary)
    }),
    route('get', '/variants/:variantId/slides', ({ param, url }) => {
      const deck = deckFor(param('variantId'))
      if (!deck) throw notFound('Variant', param('variantId'))
      const query = url.searchParams
      const cursor = Number(query.get('cursor') ?? 0)
      const limit = Number(query.get('limit') ?? 100)
      if (!Number.isSafeInteger(cursor) || cursor < 0 || !Number.isSafeInteger(limit) || limit < 1 || limit > 500)
        return json({ error: { code: 'validation_error', message: 'Не удалось прочитать страницу слайдов' } }, 422)
      return json({
        items: deck.slides.slice(cursor, cursor + limit),
        next_cursor: cursor + limit < deck.slides.length ? String(cursor + limit) : null,
      })
    }),
    route('get', '/projects/:projectId/agent/thread', ({ param }) => json(thread(param('projectId')))),
    route('delete', '/projects/:projectId/agent/thread', ({ param }) => {
      const current = thread(param('projectId'))
      if (current.active_job_id)
        throw stateConflict('Ещё отвечаю на прошлое сообщение', { job_id: current.active_job_id })
      threads.set(param('projectId'), { items: [], active_job_id: null })
      save()
      return noContent()
    }),
    route('post', '/projects/:projectId/agent/messages', async ({ request, param }) => {
      const projectId = param('projectId')
      const current = thread(projectId)
      if (current.active_job_id)
        throw stateConflict('Ещё отвечаю на прошлое сообщение', { job_id: current.active_job_id })
      const raw = await readJson(request)
      if (typeof raw.text !== 'string' || !raw.text.trim()) throw validationError('text', 'Напишите сообщение')
      if ('use_llm' in raw) throw validationError('use_llm', 'Лишнее поле')
      const body = raw as unknown as Body
      const id = prefixedId('job_agent')
      const user: Message = {
        id: prefixedId('msg'),
        role: 'user',
        text: body.text,
        created_at: nowIso(),
        job_id: id,
        ...(body.variant_id ? { variant_id: body.variant_id } : {}),
        ...(body.plan_id ? { plan_id: body.plan_id } : {}),
        ...(body.slide_id ? { slide_id: body.slide_id } : {}),
      }
      const deck = body.variant_id ? deckFor(body.variant_id) : null
      const target =
        deck?.slides.find((item) => item.id === body.slide_id || item.slide_plan_id === body.slide_id) ??
        deck?.slides[0]
      const slide = target ? Number(target.index) + (deck?.slides.some((item) => item.index === 0) ? 1 : 0) : null
      const revision = Math.max(1, ...(deck?.slides.map((item) => Number(item.revision ?? 1)) ?? [1])) + 1
      const question =
        /\?/.test(body.text) && !/сделай|добавь|найди|покажи|убери|удали|сократи|отмени|перепиши/i.test(body.text)
      const research = /интернет|цифр|рын[ок]/i.test(body.text)
      const undo = /отмен[аи]/i.test(body.text)
      const actions: Action[] = []
      const frames: Frame[] = [{ type: 'agent.status', data: { text: 'Думаю…', step: 1 } }]
      if (!question) {
        if (research) {
          const action: Action = {
            tool: 'web_research',
            slide,
            ok: true,
            summary: 'Нашёл источник для проверки цифр.',
            sources: [{ title: 'Новости рынка технологий', url: 'https://www.cnews.ru/', publisher: 'CNews' }],
          }
          actions.push(action)
          frames.push(
            {
              type: 'agent.action',
              data: { phase: 'started', tool: action.tool, slide, text: 'Ищу в интернете: свежие цифры по рынку' },
            },
            {
              type: 'agent.action',
              data: { phase: 'finished', ...action, text: 'Ищу в интернете: свежие цифры по рынку' },
            },
          )
        }
        const action: Action = {
          tool: undo ? 'undo' : body.variant_id ? 'rewrite_slide' : body.plan_id ? 'edit_plan' : 'update_brief',
          slide,
          ok: true,
          summary: undo
            ? 'Вернул предыдущую правку.'
            : body.variant_id
              ? 'Сделал слайд короче.'
              : body.plan_id
                ? 'Обновил план.'
                : 'Обновил задачу презентации.',
          revision,
        }
        actions.push(action)
        frames.push(
          {
            type: 'agent.action',
            data: {
              phase: 'started',
              tool: action.tool,
              slide,
              text: undo
                ? 'Возвращаю предыдущую правку'
                : body.variant_id
                  ? 'Сокращаю текст слайда'
                  : 'Уточняю содержание презентации',
            },
          },
          { type: 'agent.action', data: { phase: 'finished', ...action, text: action.summary } },
        )
        if (target && body.variant_id) {
          const artifactId = prefixedId('art_agent')
          frames.push(
            {
              type: 'slide.updated',
              data: {
                variant_id: body.variant_id,
                slide_id: target.id,
                slide_key: `${body.variant_id}:${target.index}`,
                index: target.index,
                revision,
                preview_revision: revision,
                preview_artifact_id: artifactId,
                status: 'rendered',
              },
            },
            {
              type: 'deck.revision',
              data: {
                variant_id: body.variant_id,
                revision,
                deck_artifact_id: deck?.summary.deck_artifact_id,
                slide_count: deck?.slides.length,
                structural: false,
              },
            },
          )
        }
      }
      const assistant: Message = {
        id: prefixedId('msg'),
        role: 'assistant',
        text: question
          ? 'Что важнее для этой презентации?'
          : undo
            ? 'Вернул предыдущую правку.'
            : 'Сохранил изменения. Посмотрите, так лучше?',
        created_at: nowIso(),
        job_id: id,
        reply_to: user.id,
        mode: 'local',
        actions,
        revision: question ? null : revision,
        question: question
          ? { text: 'На чём сделать акцент?', options: ['Сделай этот слайд короче', 'Найди свежие цифры по рынку'] }
          : null,
      }
      frames.push(
        { type: 'agent.status', data: { text: 'Проверяю результат…', step: 2 } },
        { type: 'agent.message', data: { message: assistant } },
        { type: 'job.completed', data: { state: 'completed', message_id: assistant.id, revision } },
      )
      current.items.push(user)
      current.active_job_id = id
      const run: Run = {
        id,
        projectId,
        body,
        frames,
        events: [],
        applied: false,
        canceled: false,
        startedAt: Date.now(),
      }
      runs.set(id, run)
      save()
      schedule(run)
      return json(
        {
          job_id: id,
          message_id: user.id,
          events_url: `${API_BASE}/jobs/${id}/events`,
          thread_url: `${API_BASE}/projects/${projectId}/agent/thread`,
        },
        202,
      )
    }),
    route('post', '/projects/:projectId/agent/jobs/:jobId/cancel', ({ param }) => {
      const run = runs.get(param('jobId'))
      if (!run || run.projectId !== param('projectId')) throw notFound('Job', param('jobId'))
      if (thread(run.projectId).active_job_id !== run.id) return noContent()
      run.canceled = true
      const message: Message = {
        id: prefixedId('msg'),
        role: 'assistant',
        text: 'Остановился. Уже сделанные правки сохранил.',
        created_at: nowIso(),
        job_id: run.id,
        mode: 'local',
        actions: run.events
          .filter((event) => event.type === 'agent.action' && event.data.phase === 'finished')
          .map((event) => event.data as unknown as Action),
      }
      emit(run, { type: 'agent.message', data: { message } })
      emit(run, { type: 'job.completed', data: { state: 'canceled', message_id: message.id } })
      return noContent()
    }),
    http.get(`*${API_BASE}/jobs/:jobId/events`, ({ params, request }) => {
      const run = runs.get(String(params.jobId))
      if (!run) return
      let cursor = Math.max(
        Number(new URL(request.url).searchParams.get('after') ?? 0),
        Number(request.headers.get('Last-Event-ID') ?? 0),
      )
      let canceled = false
      return new HttpResponse(
        new ReadableStream({
          async pull(controller) {
            while (!canceled) {
              const frame = run.events[cursor]
              if (frame) {
                cursor++
                controller.enqueue(
                  new TextEncoder().encode(
                    `id: ${cursor}\nevent: ${frame.type}\ndata: ${JSON.stringify({ sequence: cursor, type: frame.type, data: frame.data })}\n\n`,
                  ),
                )
                if (frame.type === 'job.completed' || frame.type === 'job.failed') controller.close()
                return
              }
              await delay(40)
            }
          },
          cancel() {
            canceled = true
          },
        }),
        { headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' } },
      )
    }),
    http.get(`*${API_BASE}/artifacts/:artifactId/download`, ({ params }) => {
      const revision = previews.get(String(params.artifactId))
      if (!revision) return
      return new HttpResponse(
        `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720"><rect width="1280" height="720" fill="#ffffff"/><rect width="18" height="720" fill="#0066ff"/><text x="80" y="180" font-family="sans-serif" font-size="48" fill="#17202a">Главное — рост и результат</text><text x="80" y="290" font-family="sans-serif" font-size="28" fill="#495563">Сократил до главного вывода</text><text x="80" y="650" font-family="sans-serif" font-size="18" fill="#495563">Ревизия ${revision}</text></svg>`,
        { headers: { 'Content-Type': 'image/svg+xml' } },
      )
    }),
  ]
}
