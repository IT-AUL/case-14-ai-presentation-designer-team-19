import { ApiError } from '@/shared/api'
import journal from '@/shared/api/mocks/fixtures/assistant/data-generation-events.json'
import type { JobEvent } from '../model/types'
import { createSseParser } from '../lib/sse'
import { subscribeJobEvents } from './events'

const frame = (sequence: number, event: string, extra = {}) => `id: ${sequence}\nevent: ${event}\ndata: ${JSON.stringify({ sequence, event, ...extra })}\n\n`
const response = (content: string) => new Response(content, { headers: { 'Content-Type': 'text/event-stream' } })
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })

it('decodes the real CRLF journal and preserves top-level payload data', async () => {
  const events: JobEvent[] = []
  const fetcher = vi.fn().mockResolvedValue(response(journal.body.sse))
  vi.stubGlobal('fetch', fetcher)
  await subscribeJobEvents('job', { onEvent: (event) => events.push(event) })
  expect(events.at(-1)?.type).toBe('job.completed')
  expect(events.find((event) => event.type === 'slide.ready')?.data).toHaveProperty('slide_id')
  expect(fetcher).toHaveBeenCalledTimes(1)
})
it('resumes after EOF with cursor in query and Last-Event-ID and ignores replayed duplicates', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(response(frame(3, 'stage.started'))).mockResolvedValueOnce(response(frame(3, 'stage.started') + frame(4, 'job.completed')))
  vi.stubGlobal('fetch', fetcher)
  const events: JobEvent[] = []
  await subscribeJobEvents('job x', { after: 2, onEvent: (event) => events.push(event) })
  expect(events.map((event) => event.sequence)).toEqual([3, 4])
  expect(fetcher.mock.calls[0]?.[0]).toContain('job%20x/events?after=2')
  expect(fetcher.mock.calls[1]?.[0]).toContain('after=3')
  expect(fetcher.mock.calls[1]?.[1]).toHaveProperty('headers.Last-Event-ID', '3')
})
it('reconnects after a rejected connection and after a reader interruption', async () => {
  let brokenController: ReadableStreamDefaultController<Uint8Array> | undefined
  const stream = new ReadableStream<Uint8Array>({ start(controller) { brokenController = controller; controller.enqueue(new TextEncoder().encode(frame(1, 'stage.started'))) } })
  const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('offline')).mockResolvedValueOnce(new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })).mockResolvedValueOnce(response(frame(2, 'job.failed')))
  vi.stubGlobal('fetch', fetcher)
  const events: JobEvent[] = []
  await subscribeJobEvents('job', { onEvent: (event) => { events.push(event); if (event.sequence === 1) brokenController?.error(new TypeError('connection closed')) } })
  expect(events.map((event) => event.type)).toEqual(['stage.started', 'job.failed'])
  expect(fetcher.mock.calls[2]?.[1]).toHaveProperty('headers.Last-Event-ID', '1')
})
it.each(['job.completed', 'job.failed', 'job.canceled'])('cancels a still-open stream at %s', async (event) => {
  const cancel = vi.fn()
  const stream = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(frame(1, event) + frame(2, 'slide.ready'))) }, cancel })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })))
  const events: JobEvent[] = []
  await subscribeJobEvents('job', { onEvent: (event) => events.push(event) })
  expect(events).toHaveLength(1)
  expect(cancel).toHaveBeenCalledOnce()
})
it('aborts an open reader without reconnecting or canceling the job', async () => {
  const abort = new AbortController()
  const cancel = vi.fn()
  const stream = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(frame(1, 'stage.started'))) }, cancel })
  const fetcher = vi.fn().mockResolvedValue(new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } }))
  vi.stubGlobal('fetch', fetcher)
  const subscription = subscribeJobEvents('job', { signal: abort.signal, onEvent: () => abort.abort() })
  await expect(subscription).rejects.toHaveProperty('name', 'AbortError')
  expect(cancel).toHaveBeenCalledOnce()
  expect(fetcher).toHaveBeenCalledOnce()
})
it('honors an already-aborted signal before fetching', async () => {
  const controller = new AbortController()
  controller.abort()
  const fetcher = vi.fn()
  vi.stubGlobal('fetch', fetcher)
  await expect(subscribeJobEvents('job', { signal: controller.signal, onEvent: vi.fn() })).rejects.toHaveProperty('name', 'AbortError')
  expect(fetcher).not.toHaveBeenCalled()
})
it.each([404, 405, 501])('degrades quietly when events are unavailable: %i', async (status) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status })))
  await expect(subscribeJobEvents('job', { onEvent: vi.fn() })).resolves.toBeUndefined()
})
it('does not retry a validation failure', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response('{}', { status: 422 }))
  vi.stubGlobal('fetch', fetcher)
  await expect(subscribeJobEvents('job', { onEvent: vi.fn() })).rejects.toBeInstanceOf(ApiError)
  expect(fetcher).toHaveBeenCalledOnce()
})
it('reports an exhausted connection after bounded reconnects', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('offline')))
  const subscription = subscribeJobEvents('job', { onEvent: vi.fn() })
  const expectation = expect(subscription).rejects.toThrow('offline')
  await vi.runAllTimersAsync()
  await expectation
})
it('parses split UTF-8 text, multiline data, comments and malformed records without losing the cursor', () => {
  const events: JobEvent[] = []
  const parse = createSseParser((event) => events.push(event))
  const input = ': heartbeat\r\n\r\nid: 1\r\nevent: slide.ready\r\ndata: {"sequence":1,\r\ndata: "message":"Готово"}\r\n\r\nid: 2\r\ndata: invalid\r\n\r\n' + frame(3, 'job.completed')
  for (const char of input) parse(char)
  expect(events.map((event) => event.sequence)).toEqual([1, 3])
  expect(events[0]?.message).toBe('Готово')
})

it('keeps UTF-8 characters when a transport chunk splits a code point', async () => {
  const bytes = new TextEncoder().encode(frame(1, 'job.completed', { message: 'Готово' }))
  const stream = new ReadableStream({ start(controller) { for (const byte of bytes) controller.enqueue(new Uint8Array([byte])); controller.close() } })
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream, { headers: { 'Content-Type': 'text/event-stream' } })))
  const events: JobEvent[] = []
  await subscribeJobEvents('job', { onEvent: (event) => events.push(event) })
  expect(events[0]?.message).toBe('Готово')
})
