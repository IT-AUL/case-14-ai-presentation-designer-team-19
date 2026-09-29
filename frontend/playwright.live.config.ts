import { defineConfig, devices } from '@playwright/test'

const PORT = Number(process.env.E2E_LIVE_PORT ?? 4318)
const BACKEND_URL = process.env.BACKEND_URL ?? 'http://localhost:8000'

export default defineConfig({
  testDir: './e2e-live',
  outputDir: './test-results-live',
  fullyParallel: false,
  workers: 1,
  timeout: 300_000,
  expect: { timeout: 20_000 },
  reporter: [['list']],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    locale: 'ru-RU',
    timezoneId: 'Europe/Moscow',
    viewport: { width: 1440, height: 900 },
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } } }],
  webServer: {
    command: `npx vite build --outDir dist-live --emptyOutDir && npx vite preview --outDir dist-live --host 127.0.0.1 --port ${PORT} --strictPort`,
    url: `http://127.0.0.1:${PORT}`,
    env: { BACKEND_URL },
    reuseExistingServer: true,
    timeout: 180_000,
    stdout: 'ignore',
    stderr: 'pipe',
  },
})
