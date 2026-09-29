import { API_BASE } from '@/shared/config'

export interface SeededRun {
  projectId: string
  runId: string
}

function fileNameFrom(response: Response, fallback: string): string {
  const disposition = response.headers.get('content-disposition') ?? ''
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1]
  if (encoded) return decodeURIComponent(encoded)
  return /filename="?([^";]+)"?/i.exec(disposition)?.[1] ?? fallback
}

async function downloadThroughWorker(anchor: HTMLAnchorElement): Promise<void> {
  const response = await fetch(anchor.href)
  if (!response.ok) throw new Error(`Download failed with ${response.status}`)
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = anchor.getAttribute('download') || fileNameFrom(response, 'download')
  document.body.append(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000)
}

function routeApiDownloadsThroughWorker(): void {
  document.addEventListener(
    'click',
    (event) => {
      const anchor = event.target instanceof Element ? event.target.closest<HTMLAnchorElement>('a[download]') : null
      if (!anchor || !new URL(anchor.href, window.location.href).pathname.startsWith(API_BASE)) return
      event.preventDefault()
      void downloadThroughWorker(anchor)
    },
    true,
  )
}

export async function startMockWorker(): Promise<SeededRun[]> {
  const [{ startMockWorker: start }, { generationFixture }] = await Promise.all([import('./mocks/browser'), import('./mocks/fixtures')])
  await start()
  routeApiDownloadsThroughWorker()
  return [{ projectId: generationFixture.project_id, runId: generationFixture.id }]
}
