export const RECONNECT_DELAYS_MS: readonly number[] = [3000, 6000, 10000]

const MAX_RECONNECT_DELAY_MS = 10000

export interface PollableState<T> {
  status: 'pending' | 'error' | 'success'
  data: T | undefined
  dataUpdateCount: number
  errorUpdateCount: number
}

export interface PollableQuery<T> {
  state: PollableState<T>
}

interface StreakBaseline {
  dataUpdateCount: number
  errorUpdateCount: number
}

const baselines = new WeakMap<object, StreakBaseline>()

export function failureStreak<T>(query: PollableQuery<T>): number {
  const { status, dataUpdateCount, errorUpdateCount } = query.state
  let baseline = baselines.get(query)
  if (!baseline || baseline.dataUpdateCount !== dataUpdateCount) {
    baseline = { dataUpdateCount, errorUpdateCount: status === 'error' ? errorUpdateCount - 1 : errorUpdateCount }
    baselines.set(query, baseline)
  }
  return status === 'error' ? Math.max(1, errorUpdateCount - baseline.errorUpdateCount) : 0
}

export function reconnectDelay(streak: number): number {
  const index = Math.min(Math.max(streak, 1), RECONNECT_DELAYS_MS.length) - 1
  return RECONNECT_DELAYS_MS[index] ?? MAX_RECONNECT_DELAY_MS
}

export function pollWithBackoff<T>(query: PollableQuery<T>, shouldPoll: (data: T | undefined) => boolean, intervalMs: number): number | false {
  const streak = failureStreak(query)
  const { status, data } = query.state
  if (status === 'error') return data === undefined || shouldPoll(data) ? reconnectDelay(streak) : false
  return shouldPoll(data) ? intervalMs : false
}

export function isTransientError(error: unknown): boolean {
  if (!(error instanceof Error)) return error !== null && error !== undefined
  const status = (error as { status?: unknown }).status
  if (typeof status !== 'number') return true
  return status === 0 || status === 408 || status === 429 || status >= 500
}
