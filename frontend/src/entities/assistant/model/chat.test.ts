import { emptyAgentRun, reduceAgentRun } from './chat'
import { normalizeJobEvent } from './normalize'
function event(type: string, data: Record<string, unknown>, sequence = 1) {
  const result = normalizeJobEvent({ sequence, type, data })
  if (!result) throw new Error('Invalid event')
  return result
}
it('matches repeated tool executions to the correct started step and retains their order', () => {
  let state = reduceAgentRun(
    emptyAgentRun,
    event('agent.action', { phase: 'started', tool: 'rewrite_slide', slide: 2, text: 'Первый' }),
  )
  state = reduceAgentRun(
    state,
    event('agent.action', { phase: 'finished', tool: 'rewrite_slide', slide: 2, ok: true, summary: 'Первый готов' }, 2),
  )
  state = reduceAgentRun(
    state,
    event('agent.action', { phase: 'started', tool: 'rewrite_slide', slide: 2, text: 'Второй' }, 3),
  )
  state = reduceAgentRun(
    state,
    event(
      'agent.action',
      { phase: 'finished', tool: 'rewrite_slide', slide: 2, ok: false, summary: 'Второй не вышел' },
      4,
    ),
  )
  expect(state.steps).toMatchObject([
    { text: 'Первый', summary: 'Первый готов', ok: true },
    { text: 'Второй', summary: 'Второй не вышел', ok: false },
  ])
})
it('retains an early final message but completes only on the terminal event', () => {
  let state = reduceAgentRun(emptyAgentRun, event('agent.status', { text: 'Думаю' }))
  state = reduceAgentRun(
    state,
    event('agent.message', { message: { id: 'm', text: 'Не получилось', role: 'assistant', mode: 'error' } }, 2),
  )
  expect(state.done).toBe(false)
  state = reduceAgentRun(state, event('job.failed', { error: { message: 'Нет связи' } }, 3))
  expect(state).toMatchObject({ done: true, failed: true, status: null, message: { text: 'Не получилось' } })
})
it('handles standalone finished actions and unrelated events without inventing text', () => {
  const state = reduceAgentRun(
    emptyAgentRun,
    event('agent.action', {
      phase: 'finished',
      tool: 'web_research',
      slide: null,
      ok: true,
      summary: 'Нашёл',
      sources: [{ title: 'Отчёт', url: 'https://example.com' }],
    }),
  )
  expect(state.steps).toHaveLength(1)
  expect(state.steps[0]).toMatchObject({ text: '', summary: 'Нашёл', sources: [{ title: 'Отчёт' }] })
  expect(reduceAgentRun(state, event('unknown', {}, 2))).toEqual({ ...state, lastSequence: 2 })
})
