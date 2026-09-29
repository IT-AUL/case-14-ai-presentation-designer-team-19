import { Link } from 'react-router'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { slidePreviewUrl, type SlideInfo } from '@/entities/variant'
import { RequestPdfExport } from '@/features/request-pdf-export'
import { pluralize } from '@/shared/lib/format'
import { PdfPage, Skeleton } from '@/shared/ui'
import { slideLabel } from '../lib/slides'
import styles from './VariantThumbnails.module.css'

const SKELETON_TILES = 6

interface VariantThumbnailsProps {
  variantId: string
  variantName: string
  slides: SlideInfo[] | undefined
  slidesError: Error | null
  pdfUrl: string | null
  filesPending: boolean
  slideHref: (slideNumber: number) => string
}

export function VariantThumbnails({ variantId, variantName, slides, slidesError, pdfUrl, filesPending, slideHref }: VariantThumbnailsProps) {
  const renderPreviews = useCapabilityFlag(FEATURE_PATHS.pngPreviews)
  if (slidesError) {
    return (
      <p className={styles.message} role="alert">
        Не удалось загрузить слайды: {slidesError.message}
      </p>
    )
  }

  if (!slides || filesPending) {
    return (
      <div className={styles.grid} aria-busy="true" aria-label="Загружаем миниатюры">
        {Array.from({ length: SKELETON_TILES }, (_, index) => (
          <Skeleton key={index} className={styles.skeleton} delay={index * 0.08} />
        ))}
      </div>
    )
  }

  if (slides.length === 0) {
    return <p className={styles.message}>В варианте нет слайдов.</p>
  }

  if (!pdfUrl && !slides.every((slide) => slidePreviewUrl(slide, renderPreviews))) {
    return (
      <div className={styles.placeholder}>
        <p className={styles.placeholderText}>
          {slides.length} {pluralize(slides.length, ['слайд', 'слайда', 'слайдов'])} · превью появятся после сборки PDF.
        </p>
        <RequestPdfExport variantId={variantId} variantName={variantName} />
      </div>
    )
  }

  return (
    <ul className={styles.grid} aria-label={`Слайды варианта «${variantName}»`}>
      {slides.map((slide) => (
        <li key={slide.id}>
          <Link className={styles.thumb} to={slideHref(slide.index + 1)} aria-label={`${slideLabel(slide)}. Открыть в аудите`}>
            <PdfPage url={pdfUrl} imageUrl={slidePreviewUrl(slide, renderPreviews)} pageNumber={slide.index + 1} label={slideLabel(slide)}>
              <span className={styles.number} aria-hidden>
                {slide.index + 1}
              </span>
            </PdfPage>
          </Link>
        </li>
      ))}
    </ul>
  )
}
