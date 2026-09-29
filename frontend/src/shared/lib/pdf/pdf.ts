import type * as PdfJsModule from 'pdfjs-dist/legacy/build/pdf.mjs'
import type { PDFDocumentProxy } from 'pdfjs-dist/legacy/build/pdf.mjs'

type PdfJs = typeof PdfJsModule

let pdfjs: Promise<PdfJs> | null = null

function loadPdfJs(): Promise<PdfJs> {
  pdfjs ??= Promise.all([import('pdfjs-dist/legacy/build/pdf.mjs'), import('pdfjs-dist/legacy/build/pdf.worker.min.mjs?url')])
    .then(([library, worker]) => {
      library.GlobalWorkerOptions.workerSrc = worker.default
      return library
    })
    .catch((error: unknown) => {
      pdfjs = null
      throw error
    })
  return pdfjs
}

export async function loadPdf(url: string): Promise<PDFDocumentProxy> {
  const { getDocument } = await loadPdfJs()
  return getDocument({ url }).promise
}

export type { PDFDocumentProxy }
