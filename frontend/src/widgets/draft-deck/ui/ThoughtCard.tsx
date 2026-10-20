import type { UnderstandingBlock, UnderstandingDataset } from '@/entities/assistant'
import { Badge, Button } from '@/shared/ui'
import { blockTitle } from '../lib/table'
import { BLOCK_KIND_LABEL, BLOCK_KIND_TONE, blockOrigin, extractFigure, normalizeBlockKind, sanitizeAssistantCopy } from '../lib/understanding'
import styles from './DraftDeck.module.css'

export interface ThoughtCardProps {
  block: UnderstandingBlock
  datasets: readonly UnderstandingDataset[]
  selected?: boolean
  onOpen?: () => void
  onEdit?: () => void
  onRemove?: (blockId: string) => void
}

export function ThoughtCard({ block, datasets, selected, onOpen, onEdit, onRemove }: ThoughtCardProps) {
  const kindKey = normalizeBlockKind(block.kind)
  const kindLabel = BLOCK_KIND_LABEL[kindKey]
  const origin = blockOrigin(block, datasets)
  const cardTitle = sanitizeAssistantCopy(blockTitle(block))
  const title = block.title?.trim() ? sanitizeAssistantCopy(block.title.trim()) : cardTitle
  const bodyText = block.text ? sanitizeAssistantCopy(block.text) : null
  const figure = extractFigure(bodyText ?? title)
  const originText = origin.kind === 'user' ? null : origin.label

  return (
    <article className={styles.thought} role="listitem" data-selected={selected || undefined} aria-label={`Блок: ${cardTitle}`}>
      <header className={styles.thoughtMeta}>
        {kindKey !== 'number' && <Badge tone={BLOCK_KIND_TONE[kindKey]}>{kindLabel}</Badge>}
        {originText && <span className={styles.thoughtOrigin}>{originText}</span>}
      </header>
      <div className={styles.thoughtMain} data-visual={figure ? 'figure' : 'none'}>
        <div className={styles.thoughtBody}>
          <h3 className={styles.thoughtTitle}>
            {onOpen ? (
              <button type="button" className={styles.insightOpen} onClick={onOpen} aria-label={`Открыть: ${cardTitle}`}>
                {title}
              </button>
            ) : (
              title
            )}
          </h3>
          {bodyText && bodyText !== title && <p className={styles.thoughtText}>{bodyText}</p>}
        </div>
        {figure && (
          <span className={`${styles.figure} ${styles.thoughtFigure}`}>{figure}</span>
        )}
      </div>
      {(onEdit || onRemove) && (
        <footer className={styles.thoughtActions}>
          {onEdit && (
            <Button variant="ghost" size="sm" onClick={onEdit}>
              Изменить
            </Button>
          )}
          {onRemove && (
            <Button variant="ghost" size="sm" onClick={() => onRemove(block.id)}>
              Не включать
            </Button>
          )}
        </footer>
      )}
    </article>
  )
}
