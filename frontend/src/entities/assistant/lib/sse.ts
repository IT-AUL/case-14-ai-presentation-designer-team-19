import { normalizeJobEvent } from '../model/normalize'
import type { JobEvent } from '../model/types'

export function isTerminalEvent(event: JobEvent): boolean {
  return ['job.completed', 'job.failed', 'job.canceled', 'job.cancelled'].includes(event.type)
    || (event.type === 'job.state_changed' && ['completed', 'failed', 'canceled', 'cancelled'].includes(String(event.state ?? event.data.state)))
}

export function createSseParser(onEvent: (event: JobEvent) => void) {
  let buffer = ''
  let eventType = ''
  let id = ''
  let data: string[] = []
  function line(value: string) {
    if (value === '') {
      if (data.length) {
        try {
          const event = normalizeJobEvent(JSON.parse(data.join('\n')), eventType || undefined, id || undefined)
          if (event) onEvent(event)
        } catch (error) {
          if (!(error instanceof SyntaxError)) throw error
        }
      }
      eventType = ''
      id = ''
      data = []
      return
    }
    if (value.startsWith(':')) return
    const colon = value.indexOf(':')
    const field = colon === -1 ? value : value.slice(0, colon)
    const rest = colon === -1 ? '' : value.slice(colon + 1).replace(/^ /, '')
    if (field === 'data') data.push(rest)
    if (field === 'event') eventType = rest
    if (field === 'id' && !rest.includes('\0')) id = rest
  }
  return (chunk: string) => {
    buffer += chunk
    for (;;) {
      const match = /\r\n|\r|\n/.exec(buffer)
      if (!match || (match[0] === '\r' && match.index === buffer.length - 1)) return
      line(buffer.slice(0, match.index))
      buffer = buffer.slice(match.index + match[0].length)
    }
  }
}
