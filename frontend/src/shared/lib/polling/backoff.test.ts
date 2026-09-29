import { describe, expect, it } from 'vitest'
import { isTransientError, pollWithBackoff, reconnectDelay, type PollableState } from './backoff'

function query<T>(state: Partial<PollableState<T>>) {
  return { state: { status: 'success', data: undefined, dataUpdateCount: 0, errorUpdateCount: 0, ...state } as PollableState<T> }
}

const always = () => true

describe('pollWithBackoff', () => {
  it('polls at the regular interval while data is unsettled', () => {
    expect(pollWithBackoff(query({ data: 1, dataUpdateCount: 1 }), always, 2000)).toBe(2000)
    expect(pollWithBackoff(query({ data: 1, dataUpdateCount: 1 }), () => false, 2000)).toBe(false)
  })

  it('backs off 3s, 6s, then 10s while errors repeat and resets after a success', () => {
    const target = query<number>({ data: 1, dataUpdateCount: 1 })
    expect(pollWithBackoff(target, always, 2000)).toBe(2000)
    target.state = { ...target.state, status: 'error', errorUpdateCount: 1 }
    expect(pollWithBackoff(target, always, 2000)).toBe(3000)
    target.state = { ...target.state, errorUpdateCount: 2 }
    expect(pollWithBackoff(target, always, 2000)).toBe(6000)
    target.state = { ...target.state, errorUpdateCount: 3 }
    expect(pollWithBackoff(target, always, 2000)).toBe(10000)
    target.state = { ...target.state, errorUpdateCount: 4 }
    expect(pollWithBackoff(target, always, 2000)).toBe(10000)
    target.state = { ...target.state, status: 'success', dataUpdateCount: 2 }
    expect(pollWithBackoff(target, always, 2000)).toBe(2000)
    target.state = { ...target.state, status: 'error', errorUpdateCount: 5 }
    expect(pollWithBackoff(target, always, 2000)).toBe(3000)
  })

  it('keeps retrying when the very first request failed', () => {
    expect(pollWithBackoff(query({ status: 'error', errorUpdateCount: 1 }), () => false, 2000)).toBe(3000)
  })

  it('stops once settled data is known even if a later refetch failed', () => {
    expect(pollWithBackoff(query({ status: 'error', data: 1, dataUpdateCount: 1, errorUpdateCount: 1 }), () => false, 2000)).toBe(false)
  })
})

describe('reconnect helpers', () => {
  it('caps the delay', () => {
    expect(reconnectDelay(0)).toBe(3000)
    expect(reconnectDelay(99)).toBe(10000)
  })

  it('treats network failures and 5xx as transient, 4xx as final', () => {
    expect(isTransientError(new TypeError('Failed to fetch'))).toBe(true)
    expect(isTransientError(Object.assign(new Error('HTTP 502'), { status: 502 }))).toBe(true)
    expect(isTransientError(Object.assign(new Error('Not found'), { status: 404 }))).toBe(false)
    expect(isTransientError(null)).toBe(false)
  })
})
