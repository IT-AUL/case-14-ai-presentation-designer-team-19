import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router'
import { rememberRun } from '@/entities/project'
import { API_MODE } from '@/shared/config'
import { AppProviders } from './setup/AppProviders'
import { router } from './router/router'
import './styles/global.css'

async function enableMocks() {
  if (API_MODE !== 'mock') return
  const { startMockWorker } = await import('@/shared/api')
  const seeded = await startMockWorker()
  seeded.forEach(({ projectId, runId }) => rememberRun(projectId, runId))
}

const container = document.getElementById('root')
if (!container) throw new Error('Root element #root is missing')

await enableMocks()

createRoot(container).render(
  <StrictMode>
    <AppProviders>
      <RouterProvider router={router} />
    </AppProviders>
  </StrictMode>,
)
