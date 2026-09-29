import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { MAX_CACHED_PDF_DOCUMENTS, trackPdfDocuments } from './usePdfDocument'

function fakeDocument() {
  return { loadingTask: { destroy: vi.fn(() => Promise.resolve()) } }
}

async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0))
}

describe('pdf document cache', () => {
  it('destroys the oldest unused documents beyond the cap', async () => {
    const client = new QueryClient()
    trackPdfDocuments(client)
    const documents = Array.from({ length: MAX_CACHED_PDF_DOCUMENTS + 2 }, fakeDocument)
    vi.useFakeTimers({ toFake: ['Date'] })
    documents.forEach((document, index) => {
      vi.setSystemTime(1_000 + index)
      client.setQueryData(['pdf', `/deck-${index}.pdf`], document)
    })
    vi.useRealTimers()
    await settle()

    expect(documents[0]?.loadingTask.destroy).toHaveBeenCalledTimes(1)
    expect(documents[1]?.loadingTask.destroy).toHaveBeenCalledTimes(1)
    expect(documents.slice(2).every((document) => document.loadingTask.destroy.mock.calls.length === 0)).toBe(true)
    expect(client.getQueryData(['pdf', '/deck-0.pdf'])).toBeUndefined()
  })

  it('destroys a document when its query is evicted', async () => {
    const client = new QueryClient()
    trackPdfDocuments(client)
    const document = fakeDocument()
    client.setQueryData(['pdf', '/deck.pdf'], document)
    client.removeQueries({ queryKey: ['pdf', '/deck.pdf'] })
    await settle()

    expect(document.loadingTask.destroy).toHaveBeenCalledTimes(1)
  })
})
