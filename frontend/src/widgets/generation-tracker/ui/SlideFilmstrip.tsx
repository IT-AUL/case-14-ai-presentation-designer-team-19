import { clsx } from 'clsx'
import { formatIndex } from '@/shared/lib/format'
import { Badge, Mono } from '@/shared/ui'
import type { FilmstripSlot } from '../lib/filmstrip'
import styles from './SlideFilmstrip.module.css'

interface SlideFilmstripProps {
  slots: readonly FilmstripSlot[]
  selectedIndex: number | null
  onSelectSlot: (index: number) => void
}

export function SlideFilmstrip({ slots, selectedIndex, onSelectSlot }: SlideFilmstripProps) {
  if (slots.length === 0) return null

  const readyCount = slots.filter((slot) => slot.state === 'ready').length

  return (
    <section className={styles.section} aria-label="Лента слайдов">
      <div className={styles.header}>
        <span className={styles.heading}>Лента слайдов колоды</span>
        <Mono className={styles.counter}>
          Готово {readyCount} из {slots.length}
        </Mono>
      </div>
      <ol className={styles.grid} aria-label="Слайды колоды">
        {slots.map((slot) => {
          const isReady = slot.state === 'ready'
          const isSelected = selectedIndex === slot.index
          const statusLabel = isReady ? 'Готов' : slot.state === 'building' ? 'Верстаю' : 'По плану'
          return (
            <li key={slot.key} className={styles.item}>
              <button
                type="button"
                disabled={!isReady}
                aria-pressed={isReady ? isSelected : undefined}
                aria-label={`Слайд ${slot.slideNumber}: ${slot.title} (${statusLabel})`}
                data-slide-id={slot.slideId}
                data-state={slot.state}
                className={clsx(
                  styles.card,
                  isSelected && styles.cardSelected,
                  slot.state === 'building' && styles.cardBuilding,
                  slot.state === 'draft' && styles.cardDraft,
                )}
                onClick={() => onSelectSlot(slot.index)}
              >
                <div className={styles.topRow}>
                  <Mono>{formatIndex(slot.slideNumber)}</Mono>
                  <Badge tone={isReady ? 'ok' : slot.state === 'building' ? 'info' : 'neutral'}>
                    {isReady ? '✓ Готов' : slot.state === 'building' ? '● Верстаю' : 'По плану'}
                  </Badge>
                </div>

                {isReady && slot.imageUrl ? (
                  <div className={styles.thumbWrap}>
                    <img
                      className={styles.thumbImage}
                      src={slot.imageUrl}
                      alt={`Слайд ${slot.slideNumber}: ${slot.title}`}
                    />
                  </div>
                ) : (
                  <div className={styles.draftBody}>
                    {slot.state === 'building' && <span className={styles.buildingPulse} aria-hidden="true" />}
                  </div>
                )}

                <span className={styles.title}>{slot.title}</span>
                {slot.purposeLabel && <span className={styles.purpose}>{slot.purposeLabel}</span>}
              </button>
            </li>
          )
        })}
      </ol>
    </section>
  )
}
