import { setupWorker } from 'msw/browser'
import { handlers } from './handlers'

let starting: Promise<void> | undefined

export function startMockWorker(): Promise<void> {
  starting ??= setupWorker(...handlers)
    .start({
      onUnhandledRequest: 'bypass',
      quiet: true,
      serviceWorker: { url: `${import.meta.env.BASE_URL}mockServiceWorker.js` },
    })
    .then(() => undefined)
  return starting
}
