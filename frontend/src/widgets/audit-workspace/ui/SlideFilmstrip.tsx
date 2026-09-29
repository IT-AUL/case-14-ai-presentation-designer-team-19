import { clsx } from 'clsx'
import type { Severity } from '@/entities/audit'
import { Check, Icon, Mono, PdfPage } from '@/shared/ui'
import styles from './SlideFilmstrip.module.css'

export interface FilmstripSlide {
  number: number
  title: string | undefined
  imageUrl?: string | null
  openCount: number
  worst: Severity | null
}

interface SlideFilmstripProps {
  slides: readonly FilmstripSlide[]
  current: number
  pdfUrl: string | null
  onPick: (slideNumber: number) => void
}

export function SlideFilmstrip({ slides, current, pdfUrl, onPick }: SlideFilmstripProps) {
  return (
    <nav aria-label="Слайды" className={styles.strip}>
      {slides.map((slide) => {
        const clean = slide.openCount === 0
        const label = `Слайд ${slide.number}${slide.title ? `: ${slide.title}` : ''} — ${clean ? 'проблем нет' : `открытых проблем: ${slide.openCount}`}`
        return (
          <button
            key={slide.number}
            type="button"
            className={styles.item}
            aria-label={label}
            aria-current={slide.number === current ? 'true' : undefined}
            onClick={() => onPick(slide.number)}
          >
            <Mono className={styles.number}>{slide.number}</Mono>
            <span className={clsx(styles.thumb, slide.number === current && styles.current)}>
              {pdfUrl || slide.imageUrl ? (
                <PdfPage url={pdfUrl} imageUrl={slide.imageUrl} pageNumber={slide.number} label={`Миниатюра слайда ${slide.number}`} />
              ) : (
                <span className={styles.placeholder} />
              )}
            </span>
            <span className={styles.badge} data-severity={clean ? 'clean' : (slide.worst ?? 'info')} aria-hidden>
              {clean ? <Icon as={Check} size={10} strokeWidth={3} /> : slide.openCount}
            </span>
          </button>
        )
      })}
    </nav>
  )
}
