import { clsx } from 'clsx'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { usePdfDocument } from '@/shared/lib/pdf'
import styles from './PdfPage.module.css'

interface PdfPageProps {
  url: string | null | undefined
  imageUrl?: string | null
  pageNumber: number
  aspectRatio?: number
  lazy?: boolean
  className?: string
  label?: string
  children?: ReactNode
}

type RenderState = 'idle' | 'ready' | 'error'

const WIDTH_STEP_PX = 32

function renderWidth(width: number): number {
  return width > 0 ? Math.ceil(width / WIDTH_STEP_PX) * WIDTH_STEP_PX : 0
}

export function PdfPage({ url, imageUrl, pageNumber, aspectRatio = 16 / 9, lazy = true, className, label, children }: PdfPageProps) {
  const frameRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const [visible, setVisible] = useState(!lazy)
  const [width, setWidth] = useState(0)
  const [ratio, setRatio] = useState(aspectRatio)
  const [state, setState] = useState<RenderState>('idle')
  const [failedImage, setFailedImage] = useState<string | null>(null)
  const image = imageUrl && imageUrl !== failedImage ? imageUrl : null
  const { data: pdf, isError } = usePdfDocument(visible && !image ? url : null)

  const [shownImage, setShownImage] = useState(image)
  if (shownImage !== image) {
    setShownImage(image)
    setState('idle')
  }

  useEffect(() => {
    const frame = frameRef.current
    if (!frame) return
    let pendingFrame = 0
    const resize = new ResizeObserver((entries) => {
      const next = renderWidth(entries[0]?.contentRect.width ?? 0)
      cancelAnimationFrame(pendingFrame)
      pendingFrame = requestAnimationFrame(() => setWidth(next))
    })
    resize.observe(frame)
    const intersect = visible
      ? null
      : new IntersectionObserver(
          (entries) => {
            if (entries.some((entry) => entry.isIntersecting)) setVisible(true)
          },
          { rootMargin: '200px' },
        )
    intersect?.observe(frame)
    return () => {
      cancelAnimationFrame(pendingFrame)
      resize.disconnect()
      intersect?.disconnect()
    }
  }, [visible])

  useEffect(() => {
    const canvas = canvasRef.current
    if (image || !pdf || !canvas || width === 0 || pageNumber < 1 || pageNumber > pdf.numPages) return
    let cancelled = false
    let cancelRender: (() => void) | undefined
    pdf
      .getPage(pageNumber)
      .then((page) => {
        if (cancelled) return
        const base = page.getViewport({ scale: 1 })
        const pixelRatio = Math.min(window.devicePixelRatio || 1, 2)
        const viewport = page.getViewport({ scale: (width / base.width) * pixelRatio })
        canvas.width = Math.floor(viewport.width)
        canvas.height = Math.floor(viewport.height)
        const task = page.render({ canvas, viewport })
        cancelRender = () => task.cancel()
        setRatio(base.width / base.height)
        return task.promise.then(() => {
          if (!cancelled) setState('ready')
        })
      })
      .catch((error: unknown) => {
        if (!cancelled && !(error instanceof Error && error.name === 'RenderingCancelledException')) setState('error')
      })
    return () => {
      cancelled = true
      cancelRender?.()
    }
  }, [image, pdf, pageNumber, width])

  const outOfRange = !image && Boolean(pdf) && (pageNumber < 1 || pageNumber > (pdf?.numPages ?? 0))
  const failed = image ? false : isError || outOfRange || state === 'error' || (!url && Boolean(failedImage))
  const renderState = failed ? 'error' : state
  const name = label ?? `Слайд ${pageNumber}`
  const accessibleName = failed ? `${name}: нет превью` : name

  return (
    <div ref={frameRef} className={clsx(styles.frame, className)} style={{ aspectRatio: ratio }} data-state={renderState}>
      {image ? (
        visible ? (
          <img
            className={styles.canvas}
            src={image}
            alt={accessibleName}
            decoding="async"
            data-state={renderState}
            onLoad={(event) => {
              const { naturalWidth, naturalHeight } = event.currentTarget
              if (naturalWidth > 0 && naturalHeight > 0) setRatio(naturalWidth / naturalHeight)
              setState('ready')
            }}
            onError={() => setFailedImage(image)}
          />
        ) : (
          <div className={styles.canvas} role="img" aria-label={accessibleName} data-state={renderState} />
        )
      ) : (
        <canvas ref={canvasRef} className={styles.canvas} role="img" aria-label={accessibleName} data-state={renderState} />
      )}
      {failed && (
        <span className={styles.fallback} aria-hidden>
          Нет превью
        </span>
      )}
      {children && <div className={styles.overlay}>{children}</div>}
    </div>
  )
}
