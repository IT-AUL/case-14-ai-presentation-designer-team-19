import type { Understanding } from '@/entities/assistant'
import { pluralize } from '@/shared/lib/format'
import { Button } from '@/shared/ui'
import { decisionProgress, materialCounts } from '../lib/table'
import styles from './DecisionPanel.module.css'

interface DecisionPanelProps {
  understanding: Understanding
  excludedInsightIds?: readonly string[]
  onConfirmAll?: () => void
  confirmAllPending?: boolean
}

export function DecisionPanel({ understanding, excludedInsightIds, onConfirmAll, confirmAllPending }: DecisionPanelProps) {
  const counts = materialCounts(understanding)
  const progress = decisionProgress(understanding, excludedInsightIds)
  const stats = [
    counts.tables > 0 && { value: counts.tables, label: pluralize(counts.tables, ['таблица', 'таблицы', 'таблиц']) },
    counts.textBlocks > 0 && { value: counts.textBlocks, label: pluralize(counts.textBlocks, ['фрагмент текста', 'фрагмента текста', 'фрагментов текста']) },
    progress.total > 0 && { value: progress.total, label: pluralize(progress.total, ['вывод', 'вывода', 'выводов']) },
  ].filter((item): item is { value: number; label: string } => Boolean(item))
  const share = progress.total > 0 ? Math.round((progress.confirmed / progress.total) * 100) : 0

  return (
    <div className={styles.panel}>
      {stats.length > 0 && (
        <dl className={styles.stats} aria-label="Что в материалах">
          {stats.map((item) => (
            <div className={styles.stat} key={item.label}>
              <dt className={styles.statLabel}>{item.label}</dt>
              <dd className={styles.statValue}>{item.value}</dd>
            </div>
          ))}
        </dl>
      )}

      {progress.total > 0 && (
        <section className={styles.progress} aria-label="Проверка выводов">
          <p className={styles.progressText}>
            {progress.pending === 0
              ? 'Все выводы отмечены'
              : `Отмечено ${progress.confirmed} из ${progress.total}`}
          </p>
          <div
            className={styles.bar}
            role="progressbar"
            aria-label="Отмечено выводов"
            aria-valuemin={0}
            aria-valuemax={progress.total}
            aria-valuenow={progress.confirmed}
          >
            <span className={styles.barFill} style={{ width: `${share}%` }} />
          </div>
          {onConfirmAll && progress.pending > 0 && (
            <Button variant="secondary" size="sm" pending={confirmAllPending} disabled={confirmAllPending} onClick={onConfirmAll}>
              Отметить все выводы ({progress.pending})
            </Button>
          )}
        </section>
      )}
    </div>
  )
}
