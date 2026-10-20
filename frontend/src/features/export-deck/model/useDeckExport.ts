import { useCallback } from 'react'
import { useCreateExport, type ExportFormat } from '@/entities/passport'
import { markEvidence } from '@/entities/project'
import type { DeckFile } from '@/entities/variant'
import { artifactUrl } from '@/shared/api'
import { downloadUrl } from '@/shared/lib/download'
import { deriveExportState, fileFromResult, type ExportState } from './exportState'

interface DeckExportOptions {
  projectId: string
  variantId: string
  format: ExportFormat
  file: DeckFile | null
  currentRevision: number | null
  supported: boolean
}

export interface DeckExport {
  state: ExportState
  create: (downloadAs?: string) => void
  markDownloaded: () => void
}

export async function downloadHtmlAsFile(url: string, fileName: string): Promise<void> {
  if (typeof fetch !== 'function' || typeof URL.createObjectURL !== 'function') {
    downloadUrl(url, fileName)
    return
  }
  try {
    const response = await fetch(url)
    if (!response.ok) {
      downloadUrl(url, fileName)
      return
    }
    const buffer = await response.arrayBuffer()
    const blob = new Blob([buffer], { type: 'application/octet-stream' })
    const objectUrl = URL.createObjectURL(blob)
    downloadUrl(objectUrl, fileName)
    setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000)
  } catch {
    downloadUrl(url, fileName)
  }
}

export function useDeckExport({ projectId, variantId, format, file, currentRevision, supported }: DeckExportOptions): DeckExport {
  const mutation = useCreateExport(variantId)
  const { mutate } = mutation
  const state = deriveExportState({
    format,
    file,
    currentRevision,
    supported,
    mutation: { status: mutation.status, data: mutation.data, error: mutation.error },
  })

  const create = useCallback(
    (downloadAs?: string) => {
      mutate([format], {
        onSuccess: (result) => {
          if (result.status !== 'created') return
          if (format === 'pptx' && projectId) markEvidence(projectId, 'exported')
          const created = fileFromResult(format, result)
          if (downloadAs && created) {
            const url = artifactUrl(created.artifactId)
            if (format === 'html') void downloadHtmlAsFile(url, downloadAs)
            else downloadUrl(url, downloadAs)
          }
        },
      })
    },
    [format, mutate, projectId],
  )

  const markDownloaded = useCallback(() => {
    if (format === 'pptx' && projectId) markEvidence(projectId, 'exported')
  }, [format, projectId])

  return { state, create, markDownloaded }
}
