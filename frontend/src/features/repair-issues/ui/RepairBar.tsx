import { Button } from '@/shared/ui'
import type { RepairPreview } from '../model/useRepairPreview'
import styles from './RepairBar.module.css'
import { RepairPreviewList } from './RepairPreviewList'

interface RepairBarProps {
  selectedCount: number
  fixableCount: number
  pending: boolean
  onSelectAll: () => void
  onClear: () => void
  onRepair: () => void
  onPreview?: () => void
  previewPending?: boolean
  preview?: RepairPreview | null
  onClosePreview?: () => void
}

export function RepairBar(props: RepairBarProps) {
  const { selectedCount, fixableCount, pending, onSelectAll, onClear, onRepair, onPreview, previewPending = false, preview, onClosePreview } = props
  const hasSelection = selectedCount > 0
  return (
    <div className={styles.stack}>
      {preview && <RepairPreviewList preview={preview} onClose={() => onClosePreview?.()} />}
      <div className={styles.bar}>
        {hasSelection ? (
          <Button size="lg" className={styles.side} onClick={onClear} disabled={pending}>
            Снять
          </Button>
        ) : (
          <Button size="lg" className={styles.side} onClick={onSelectAll} disabled={pending || fixableCount === 0}>
            Выбрать исправимые ({fixableCount})
          </Button>
        )}
        {onPreview && (
          <Button size="lg" className={styles.side} onClick={onPreview} disabled={!hasSelection || pending || previewPending} aria-busy={previewPending}>
            {previewPending ? 'Считаю…' : 'Что будет сделано'}
          </Button>
        )}
        <Button variant="primary" size="lg" className={styles.main} onClick={onRepair} disabled={!hasSelection || pending} aria-busy={pending}>
          {pending ? 'Применяю правки…' : `Исправить выбранное (${selectedCount})`}
        </Button>
      </div>
    </div>
  )
}
