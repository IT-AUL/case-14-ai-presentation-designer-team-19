import { bypass } from 'msw'
import type { RequestHandler } from 'msw'
import type { MockContext } from './context'
import { createRouter } from './http'
import type { DelayRange } from './http'
import { auditRoutes } from './routes/audits'
import { contentPackRoutes } from './routes/contentPacks'
import { exportRoutes } from './routes/exports'
import { generationRoutes } from './routes/generations'
import { projectRoutes } from './routes/projects'
import { providerSessionRoutes } from './routes/providerSessions'
import { systemRoutes } from './routes/system'
import { templateRoutes } from './routes/templates'
import { seedStore } from './seed'
import { MockStore } from './store'
import type { FixtureStrategy } from './types'

export interface MockBackendOptions {
  latency?: DelayRange
  generationDelay?: DelayRange
  seed?: boolean
  loadPdf?: (strategy: FixtureStrategy) => Promise<ArrayBuffer>
}

export interface MockBackend {
  handlers: RequestHandler[]
  store: MockStore
  ready: () => Promise<void>
}

const DEFAULT_LATENCY: DelayRange = [80, 240]
const DEFAULT_GENERATION_DELAY: DelayRange = [6000, 8000]

async function loadPublicPdf(strategy: FixtureStrategy): Promise<ArrayBuffer> {
  const url = new URL(`${import.meta.env.BASE_URL}mocks/deck-${strategy}.pdf`, globalThis.location.origin)
  const response = await fetch(bypass(url))
  if (!response.ok) throw new Error(`Failed to load ${url.pathname}: HTTP ${response.status}`)
  return response.arrayBuffer()
}

export function createMockBackend(options: MockBackendOptions = {}): MockBackend {
  const store = new MockStore()
  let seeding: Promise<void> | undefined
  const ready = () => (seeding ??= options.seed === false ? Promise.resolve() : seedStore(store))
  const router = createRouter({ latency: options.latency ?? DEFAULT_LATENCY, ready })
  const context: MockContext = {
    store,
    router,
    generationDelay: options.generationDelay ?? DEFAULT_GENERATION_DELAY,
    loadPdf: options.loadPdf ?? loadPublicPdf,
  }
  const handlers = [
    ...systemRoutes(context),
    ...providerSessionRoutes(context),
    ...projectRoutes(context),
    ...templateRoutes(context),
    ...contentPackRoutes(context),
    ...generationRoutes(context),
    ...auditRoutes(context),
    ...exportRoutes(context),
    router.fallback(),
  ]
  return { handlers, store, ready }
}

export const handlers = createMockBackend().handlers
