import type { DelayRange, Router } from './http'
import type { MockStore } from './store'
import type { FixtureStrategy } from './types'

export interface MockContext {
  store: MockStore
  router: Router
  generationDelay: DelayRange
  loadPdf: (strategy: FixtureStrategy) => Promise<ArrayBuffer>
}

export function page<T>(items: readonly T[]): { items: readonly T[]; next_cursor: null } {
  return { items, next_cursor: null }
}
