import * as Dialog from '@radix-ui/react-dialog'
import { clsx } from 'clsx'
import { formatIndex } from '@/shared/lib/format'
import { ArrowRight, Badge, Button, Check, ChevronLeft, ChevronRight, Icon, Mono, Skeleton, X } from '@/shared/ui'
import type { CatalogStrategy } from '@/entities/variant'
import { resolveStyleTitle, type StyleColumnView } from '../model/styleView'
import styles from './StyleCompareDialog.module.css'

interface StyleCompareDialogProps {
  open: boolean
  slideId: string | null
  columns: readonly StyleColumnView[]
  selectedStrategy: CatalogStrategy
  onSelectStrategy: (strategy: CatalogStrategy) => void
  onSelectSlide: (slideId: string) => void
  onOpenChange: (open: boolean) => void
  onBuildSelected: () => void
  buildDisabled?: boolean
  aspectRatio?: number | null
}

export function StyleCompareDialog({
  open,
  slideId,
  columns,
  selectedStrategy,
  onSelectStrategy,
  onSelectSlide,
  onOpenChange,
  onBuildSelected,
  buildDisabled = false,
  aspectRatio = null,
}: StyleCompareDialogProps) {
  const referenceSlots = columns[0]?.slots ?? []
  const currentIndex = Math.max(
    0,
    referenceSlots.findIndex((slot) => slot.slideId === slideId),
  )
  const currentSlot = referenceSlots[currentIndex] ?? referenceSlots[0]
  const activeSlideId = currentSlot?.slideId ?? slideId
  const selectedColumn = columns.find((col) => col.strategy === selectedStrategy) ?? columns[0]
  const selectedTitle = selectedColumn ? resolveStyleTitle(selectedColumn.strategy, selectedColumn.title) : ''

  const stepSlide = (delta: -1 | 1) => {
    if (referenceSlots.length <= 1) return
    const nextIndex = (currentIndex + delta + referenceSlots.length) % referenceSlots.length
    const nextSlot = referenceSlots[nextIndex]
    if (nextSlot) onSelectSlide(nextSlot.slideId)
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={styles.overlay} />
        <Dialog.Content
          className={styles.content}
          onKeyDown={(event) => {
            if (event.key === 'ArrowLeft') {
              event.preventDefault()
              stepSlide(-1)
            } else if (event.key === 'ArrowRight') {
              event.preventDefault()
              stepSlide(1)
            }
          }}
        >
          <header className={styles.header}>
            <div className={styles.titleGroup}>
              <Dialog.Title className={styles.title}>
                {currentSlot
                  ? `Сравнение трёх стилей · Слайд ${formatIndex(currentSlot.slideNumber)}: ${currentSlot.title}`
                  : 'Сравнение трёх стилей'}
              </Dialog.Title>
              <Dialog.Description className={styles.description}>
                Один и тот же слайд вашего плана в трёх подходах к вёрстке на вашем шаблоне.
              </Dialog.Description>
            </div>
            <div className={styles.headerActions}>
              <Dialog.Close className={styles.closeButton} aria-label="Закрыть">
                <Icon as={X} size={16} />
              </Dialog.Close>
            </div>
          </header>

          {referenceSlots.length > 1 && (
            <div className={styles.slideNav} role="navigation" aria-label="Пробные слайды">
              <div className={styles.slidePills}>
                {referenceSlots.map((slot) => {
                  const active = slot.slideId === activeSlideId
                  return (
                    <button
                      key={slot.slideId}
                      type="button"
                      className={clsx(styles.slidePill, active && styles.slidePillActive)}
                      aria-current={active ? 'true' : undefined}
                      onClick={() => onSelectSlide(slot.slideId)}
                    >
                      <Mono>{formatIndex(slot.slideNumber)}</Mono>
                      <span>{slot.purposeLabel ?? slot.title}</span>
                    </button>
                  )
                })}
              </div>
              <div className={styles.arrowControls}>
                <Button size="sm" variant="secondary" aria-label="Предыдущий слайд" onClick={() => stepSlide(-1)}>
                  <Icon as={ChevronLeft} size={14} />
                </Button>
                <Button size="sm" variant="secondary" aria-label="Следующий слайд" onClick={() => stepSlide(1)}>
                  <Icon as={ChevronRight} size={14} />
                </Button>
              </div>
            </div>
          )}

          <div className={styles.grid}>
            {columns.map((column) => {
              const slot = column.slots.find((item) => item.slideId === activeSlideId) ?? column.slots[0]
              const isSelected = column.strategy === selectedStrategy
              const title = resolveStyleTitle(column.strategy, column.title)
              return (
                <section
                  key={column.strategy}
                  className={clsx(styles.card, isSelected && styles.cardSelected)}
                  aria-label={`Стиль «${title}»`}
                >
                  <div className={styles.cardHead}>
                    <h3 className={styles.cardTitle}>{title}</h3>
                    <div className={styles.badges}>
                      {column.recommended && <Badge tone="ink">советую</Badge>}
                    </div>
                  </div>
                  <p className={styles.cardDifference}>{column.difference}</p>
                  <div className={styles.stageFrame} style={aspectRatio ? { aspectRatio: String(aspectRatio) } : undefined}>
                    {slot?.state === 'ready' && slot.imageUrl ? (
                      <img
                        className={styles.stageImage}
                        src={slot.imageUrl}
                        alt={`${title}: слайд ${slot.slideNumber} — ${slot.title}`}
                      />
                    ) : slot?.state === 'loading' ? (
                      <div className={styles.stagePlaceholder}>
                        <Skeleton style={{ width: '180px', height: '14px' }} />
                        <span>{slot.loadingCaption}</span>
                      </div>
                    ) : (
                      <div className={styles.stagePlaceholder}>
                        <strong>{slot?.title}</strong>
                        <span>Превью недоступно</span>
                      </div>
                    )}
                  </div>
                  <div className={styles.cardFooter}>
                    <Mono className={styles.exemplar}>{slot?.exemplar ?? 'Макет по правилам шаблона'}</Mono>
                    <Button
                      size="sm"
                      variant={isSelected ? 'primary' : 'secondary'}
                      onClick={() => onSelectStrategy(column.strategy)}
                    >
                      {isSelected ? (
                        <>
                          <Icon as={Check} size={14} />
                          Выбран
                        </>
                      ) : (
                        `Выбрать «${title}»`
                      )}
                    </Button>
                  </div>
                </section>
              )
            })}
          </div>

          <footer className={styles.footer}>
            <div className={styles.footerMeta}>
              Выбран стиль: <strong>{selectedTitle}</strong>
            </div>
            <div className={styles.footerActions}>
              <Dialog.Close asChild>
                <Button variant="secondary" size="md">
                  Закрыть
                </Button>
              </Dialog.Close>
              <Button
                variant="primary"
                size="md"
                disabled={buildDisabled}
                onClick={() => {
                  onOpenChange(false)
                  onBuildSelected()
                }}
              >
                Собрать в этом стиле
                <Icon as={ArrowRight} size={14} />
              </Button>
            </div>
          </footer>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
