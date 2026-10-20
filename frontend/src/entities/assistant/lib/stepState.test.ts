import { normalizeSession } from '../model/normalize'
import { stepState } from './stepState'

it('marks only explicitly completed and skipped steps', () => {
  const session = normalizeSession({ step: 4, completed_steps: [1, 2], skipped_steps: [3] })
  expect([1, 2, 3, 4, 5, 6, 7].map((step) => stepState(session, step as 1))).toEqual(['done', 'done', 'skipped', 'current', 'todo', 'todo', 'todo'])
})
it('does not infer completion from current step', () => {
  expect(stepState(normalizeSession({ step: 7 }), 2)).toBe('todo')
  expect(stepState(undefined, 1)).toBe('todo')
})
it('gives skipped precedence over completed, and current precedence over done', () => {
  const session = normalizeSession({ step: 3, completed_steps: [2, 3], skipped_steps: [2] })
  expect(stepState(session, 2)).toBe('skipped')
  expect(stepState(session, 3)).toBe('current')
})
