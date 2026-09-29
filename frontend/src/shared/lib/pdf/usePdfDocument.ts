import { useQuery, useQueryClient, type Query, type QueryClient } from '@tanstack/react-query'
import { loadPdf, type PDFDocumentProxy } from './pdf'

export const PDF_QUERY_KEY = 'pdf'
export const MAX_CACHED_PDF_DOCUMENTS = 6

const PDF_GC_TIME_MS = 5 * 60 * 1000
const trackedClients = new WeakSet<QueryClient>()

function isPdfQuery(query: Query): boolean {
  return query.queryKey[0] === PDF_QUERY_KEY
}

function isPdfDocument(value: unknown): value is PDFDocumentProxy {
  return typeof value === 'object' && value !== null && typeof (value as Partial<PDFDocumentProxy>).loadingTask?.destroy === 'function'
}

export function trimPdfDocuments(client: QueryClient, limit = MAX_CACHED_PDF_DOCUMENTS): void {
  const cache = client.getQueryCache()
  const loaded = cache
    .findAll({ queryKey: [PDF_QUERY_KEY] })
    .filter((query) => isPdfDocument(query.state.data))
    .sort((a, b) => a.state.dataUpdatedAt - b.state.dataUpdatedAt)
  let excess = loaded.length - limit
  for (const query of loaded) {
    if (excess <= 0) return
    if (query.getObserversCount() > 0) continue
    cache.remove(query)
    excess -= 1
  }
}

export function trackPdfDocuments(client: QueryClient): void {
  if (trackedClients.has(client)) return
  trackedClients.add(client)
  client.getQueryCache().subscribe((event) => {
    if (!isPdfQuery(event.query)) return
    if (event.type === 'removed') {
      const document: unknown = event.query.state.data
      if (isPdfDocument(document)) void document.loadingTask.destroy()
      return
    }
    if (event.type === 'updated' && event.action.type === 'success') queueMicrotask(() => trimPdfDocuments(client))
  })
}

export function usePdfDocument(url: string | null | undefined) {
  const client = useQueryClient()
  trackPdfDocuments(client)
  return useQuery({
    queryKey: [PDF_QUERY_KEY, url],
    queryFn: () => loadPdf(url as string),
    enabled: Boolean(url),
    staleTime: Infinity,
    gcTime: PDF_GC_TIME_MS,
  })
}
