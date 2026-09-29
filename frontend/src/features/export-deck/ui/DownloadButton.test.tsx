import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { resolveDeckFiles } from '@/entities/variant'
import type * as SharedApiModule from '@/shared/api'
import { variantFixtures } from '@/shared/api/mocks'
import { DownloadButton, type DownloadButtonProps } from './DownloadButton'

type SharedApi = typeof SharedApiModule

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
  postMock: vi.fn<(...args: unknown[]) => Promise<unknown>>(),
}))

vi.mock('@/shared/api', async (importOriginal) => {
  const actual = await importOriginal<SharedApi>()
  return { ...actual, api: { GET: getMock, POST: postMock } }
})

function respond(status: number, body: unknown) {
  const ok = status >= 200 && status < 300
  return Promise.resolve({ data: ok ? body : undefined, error: ok ? undefined : body, response: new Response(null, { status }) })
}

const notImplemented = { error: { code: 'not_implemented', message: 'Export format is not implemented', retryable: false, details: {} } }
const { variant, export: record } = variantFixtures.balanced
const files = resolveDeckFiles(variant, [record])
const projectId = 'prj_export_test'

function renderButton(props: Partial<DownloadButtonProps>) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  render(
    <QueryClientProvider client={client}>
      <DownloadButton
        projectId={projectId}
        variantId={variant.id}
        format="pdf"
        file={files.pdf}
        currentRevision={1}
        supported
        fileName="deckdna_balanced_r1.pdf"
        label="PDF"
        {...props}
      />
    </QueryClientProvider>,
  )
}

function root() {
  return document.querySelector('[data-format]') as HTMLElement
}

function evidence(): Record<string, Record<string, boolean>> {
  return JSON.parse(localStorage.getItem('deckdna.evidence.v1') ?? '{}') as Record<string, Record<string, boolean>>
}

describe('DownloadButton', () => {
  afterEach(() => {
    getMock.mockReset()
    postMock.mockReset()
    localStorage.clear()
    vi.restoreAllMocks()
  })

  it('downloads a ready file and shows its size and revision', () => {
    renderButton({})

    const link = screen.getByRole('link', { name: 'PDF' })
    expect(root()).toHaveAttribute('data-status', 'ready')
    expect(link).toHaveAttribute('href', `/api/v1/artifacts/${files.pdf?.artifactId}/download`)
    expect(link).toHaveAttribute('download', 'deckdna_balanced_r1.pdf')
    expect(screen.getByText('317 КБ · r1')).toBeInTheDocument()
  })

  it('builds a missing PPTX, downloads it at once and records the evidence', async () => {
    const user = userEvent.setup()
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    let release: () => void = () => undefined
    postMock.mockReturnValueOnce(new Promise((resolve) => (release = () => resolve(respond(202, { export_id: 'exp_new', job_id: 'job_new' })))))
    const pptxRecord = {
      ...record,
      id: 'exp_new',
      artifacts: [{ format: 'pptx', artifact_id: 'art_new_pptx', sha256: 'abcdef0123456789', size_bytes: 3_984_588, mime_type: 'application/pptx', download_url: '' }],
    }
    getMock.mockReturnValueOnce(respond(200, pptxRecord))

    renderButton({ format: 'pptx', file: null, fileName: 'deckdna_balanced_r1.pptx', label: 'Скачать PPTX', primary: true })
    expect(root()).toHaveAttribute('data-status', 'idle')

    await user.click(screen.getByRole('button', { name: 'Скачать PPTX' }))
    expect(root()).toHaveAttribute('data-status', 'creating')
    expect(screen.getByRole('button', { name: 'Собираем PPTX…' })).toBeDisabled()

    release()
    const link = await screen.findByRole('link', { name: 'Скачать PPTX' })
    expect(link).toHaveAttribute('href', '/api/v1/artifacts/art_new_pptx/download')
    expect(screen.getByText('3,8 МБ · r1')).toBeInTheDocument()
    expect(click).toHaveBeenCalledTimes(1)
    expect(postMock).toHaveBeenCalledWith('/api/v1/variants/{variant_id}/exports', expect.objectContaining({ body: { formats: ['pptx'] } }))
    expect(evidence()[projectId]?.exported).toBe(true)
  })

  it('records the exported evidence when the PPTX is downloaded', async () => {
    const user = userEvent.setup()
    renderButton({ projectId: 'prj_download_test', format: 'pptx', file: files.pptx, fileName: 'deckdna_balanced.pptx', label: 'Скачать PPTX' })

    const link = screen.getByRole('link', { name: 'Скачать PPTX' })
    link.addEventListener('click', (event) => event.preventDefault())
    await user.click(link)

    expect(evidence().prj_download_test?.exported).toBe(true)
  })

  it('keeps an unsupported format visible but inactive', () => {
    renderButton({ format: 'html', file: null, supported: false, fileName: 'deckdna_balanced.html', label: 'HTML' })

    expect(root()).toHaveAttribute('data-status', 'unavailable')
    expect(screen.getByRole('button', { name: 'HTML' })).toBeDisabled()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })

  it('rebuilds a stale file for the current revision and keeps the previous one reachable', async () => {
    const user = userEvent.setup()
    postMock.mockReturnValueOnce(respond(501, notImplemented))
    renderButton({ currentRevision: 2 })

    expect(root()).toHaveAttribute('data-status', 'idle')
    expect(screen.getByText(/соберём для r2/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'прежний r1' })).toHaveAttribute('href', `/api/v1/artifacts/${files.pdf?.artifactId}/download`)

    await user.click(screen.getByRole('button', { name: 'PDF' }))

    await waitFor(() => expect(root()).toHaveAttribute('data-status', 'unavailable'))
    expect(screen.getByText('PDF относится к прошлой ревизии')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'PDF · r1' })).toHaveAttribute('href', `/api/v1/artifacts/${files.pdf?.artifactId}/download`)
  })

  it('shows other failures and lets the user try again', async () => {
    const user = userEvent.setup()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)
    const failure = { error: { code: 'internal_error', message: 'Рендер PDF упал', retryable: true, details: {} } }
    postMock.mockReturnValueOnce(respond(500, failure)).mockReturnValueOnce(respond(202, { export_id: record.id, job_id: record.job_id }))
    getMock.mockReturnValueOnce(respond(200, record))
    renderButton({ file: null })

    await user.click(screen.getByRole('button', { name: 'PDF' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Рендер PDF упал')
    expect(root()).toHaveAttribute('data-status', 'error')

    await user.click(screen.getByRole('button', { name: 'PDF' }))
    expect(await screen.findByRole('link', { name: 'PDF' })).toBeInTheDocument()
    expect(root()).toHaveAttribute('data-status', 'ready')
  })
})
