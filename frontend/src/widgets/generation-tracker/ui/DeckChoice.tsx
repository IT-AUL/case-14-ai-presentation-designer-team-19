import { clsx } from 'clsx'
import { Link } from 'react-router'
import { useVariantFiles } from '@/features/variant-files'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { DEFAULT_STRATEGY, slidePreviewUrl, telltaleSlides, useVariantSlides, type SlideInfo } from '@/entities/variant'
import { formatClock, formatNumber, formatPercent, pluralize } from '@/shared/lib/format'
import { PdfPage, Skeleton } from '@/shared/ui'
import { resultSummary } from '../lib/scene'
import { variantElapsedSeconds, type VariantCard } from '../lib/variants'
import styles from './DeckChoice.module.css'

const STRIP_SIZE = 4

export type AuditHref = (variantId: string, slideNumber?: number) => string

function Thumb({ slide, pdfUrl, name, href, cover }: { slide: SlideInfo; pdfUrl: string | null; name: string; href: string; cover?: boolean }) {
  const renderPreviews = useCapabilityFlag(FEATURE_PATHS.pngPreviews)
  const number = slide.index + 1
  return (
    <Link className={clsx(styles.thumb, cover && styles.cover)} to={href} aria-label={cover ? `Открыть «${name}»` : `Открыть «${name}» на слайде ${number}`}>
      <PdfPage url={pdfUrl} imageUrl={slidePreviewUrl(slide, renderPreviews)} pageNumber={number} label={`${name}: слайд ${number}`} />
    </Link>
  )
}

function metricsLine(card: VariantCard): string {
  const summary = card.variant ? resultSummary(card.variant) : null
  const seconds = variantElapsedSeconds(card.variant, Date.now())
  return [
    summary?.styleFidelity != null && `${formatPercent(summary.styleFidelity)} в стиле`,
    summary?.openIssues != null && `${formatNumber(summary.openIssues)} ${pluralize(summary.openIssues, ['замечание', 'замечания', 'замечаний'])}`,
    seconds !== null && formatClock(seconds),
  ]
    .filter(Boolean)
    .join(' · ')
}

function Deck({ card, auditHref }: { card: VariantCard; auditHref: AuditHref }) {
  const variant = card.variant
  const name = card.strategy.name
  const { pdfUrl } = useVariantFiles(variant?.id)
  const { data: slides } = useVariantSlides(variant?.id)
  const recommended = card.strategy.id === DEFAULT_STRATEGY
  const [first, ...rest] = telltaleSlides(slides ?? [], STRIP_SIZE + 1)
  const failed = card.state !== 'done' || !variant

  return (
    <article className={clsx(styles.deck, recommended && styles.recommended)} aria-label={name}>
      {failed ? (
        <div className={clsx(styles.cover, styles.empty)}>Не собран</div>
      ) : first ? (
        <Thumb slide={first} pdfUrl={pdfUrl} name={name} href={auditHref(variant.id, first.index + 1)} cover />
      ) : (
        <Skeleton className={styles.cover} />
      )}
      {!failed && (
        <div className={styles.strip}>
          {rest.map((slide) => (
            <Thumb key={slide.id} slide={slide} pdfUrl={pdfUrl} name={name} href={auditHref(variant.id, slide.index + 1)} />
          ))}
        </div>
      )}
      <div className={styles.caption}>
        <div className={styles.titleRow}>
          {failed ? (
            <span className={styles.name}>{name}</span>
          ) : (
            <Link className={styles.name} to={auditHref(variant.id)}>
              {name}
            </Link>
          )}
          {recommended && <span className={styles.tag}>рекомендуем</span>}
        </div>
        <p className={styles.axis}>{card.strategy.axis}</p>
        {!failed && <p className={styles.metrics}>{metricsLine(card)}</p>}
      </div>
    </article>
  )
}

export function DeckChoice({ cards, auditHref }: { cards: readonly VariantCard[]; auditHref: AuditHref }) {
  return (
    <div className={styles.grid}>
      {cards.map((card) => (
        <Deck key={card.key} card={card} auditHref={auditHref} />
      ))}
    </div>
  )
}
