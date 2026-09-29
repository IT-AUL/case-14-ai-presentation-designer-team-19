import { plannedFix, ruleMeta, slideNumber } from '@/entities/audit'
import type { RepairPreview } from '../model/useRepairPreview'
import styles from './RepairBar.module.css'
import { Check, Icon, Minus, X } from '@/shared/ui'

const STATUS_MARK = { fixed: Check, planned: Check, failed: X, skipped: Minus } as const

export function RepairPreviewList({ preview, onClose }: { preview: RepairPreview; onClose: () => void }) {
  const willFix = (status: string | undefined) => status === 'fixed' || status === 'planned'
  const ready = preview.items.filter((item) => willFix(item.outcome?.status)).length
  return (
    <section className={styles.preview} aria-label="Что будет сделано">
      <header className={styles.previewHead}>
        <span>
          Что будет сделано · исправится {ready} из {preview.items.length}
        </span>
        <button type="button" className={styles.previewClose} onClick={onClose} aria-label="Скрыть план исправлений">
          <Icon as={X} size={14} />
        </button>
      </header>
      <ul className={styles.previewList}>
        {preview.items.map(({ issue, outcome }) => {
          const number = slideNumber(issue)
          const status = outcome?.status ?? null
          const text = outcome
            ? willFix(outcome.status)
              ? (outcome.summary ?? plannedFix(issue) ?? 'Будет исправлено')
              : (outcome.reason ?? (outcome.status === 'skipped' ? 'Сервис пропустит эту проблему' : 'Исправить не получится'))
            : 'Действие не описано'
          return (
            <li key={issue.id} className={styles.previewItem} data-status={status ?? 'unknown'}>
              <span className={styles.previewMark} aria-hidden>
                <Icon as={status ? STATUS_MARK[status] : Minus} size={14} />
              </span>
              <span className={styles.previewText}>
                <span>{text}</span>
                <span className={styles.previewMeta}>
                  {ruleMeta(issue.rule_code).name} · {number ? `сл. ${String(number).padStart(2, '0')}` : 'колода'}
                </span>
              </span>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
