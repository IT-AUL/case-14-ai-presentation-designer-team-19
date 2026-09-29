import { clsx } from 'clsx'
import { Link } from 'react-router'
import { useVariantFiles } from '@/features/variant-files'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { slidePreviewUrl, strategyInfo, telltaleSlides, useVariantSlides, type SlideInfo, type VariantSummary } from '@/entities/variant'
import { PdfPage, Skeleton } from '@/shared/ui'
import styles from './DeckPreview.module.css'

const STRIP_SIZE = 5

function Slide({ slide, pdfUrl, name, href, large }: { slide: SlideInfo; pdfUrl: string | null; name: string; href: string; large?: boolean }) {
  const renderPreviews = useCapabilityFlag(FEATURE_PATHS.pngPreviews)
  const number = slide.index + 1
  return (
    <Link className={clsx(styles.slide, large && styles.large)} to={href} aria-label={`Слайд ${number} «${name}» — открыть в аудите`}>
      <PdfPage url={pdfUrl} imageUrl={slidePreviewUrl(slide, renderPreviews)} pageNumber={number} label={`${name}: слайд ${number}`} />
    </Link>
  )
}

interface DeckPreviewProps {
  variant: VariantSummary
  auditHref: (slideNumber: number) => string
}

export function DeckPreview({ variant, auditHref }: DeckPreviewProps) {
  const name = strategyInfo(variant.strategy).name
  const { pdfUrl } = useVariantFiles(variant.id)
  const { data: slides, isPending } = useVariantSlides(variant.id)
  const [cover, ...strip] = telltaleSlides(slides ?? [], STRIP_SIZE + 1)
  const hidden = (slides?.length ?? 0) - 1 - strip.length

  return (
    <section className={styles.root} aria-label={`Колода «${name}»`}>
      {cover ? (
        <Slide slide={cover} pdfUrl={pdfUrl} name={name} href={auditHref(cover.index + 1)} large />
      ) : (
        <Skeleton className={styles.coverSkeleton} />
      )}
      <div className={styles.strip}>
        {isPending
          ? Array.from({ length: STRIP_SIZE }, (_, index) => <Skeleton key={index} className={styles.thumbSkeleton} delay={index * 0.06} />)
          : strip.map((slide) => <Slide key={slide.id} slide={slide} pdfUrl={pdfUrl} name={name} href={auditHref(slide.index + 1)} />)}
      </div>
      {hidden > 0 && <p className={styles.more}>и ещё {hidden} — все слайды в аудите</p>}
    </section>
  )
}
