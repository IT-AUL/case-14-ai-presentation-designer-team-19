import { Link } from 'react-router'
import { pluralize } from '@/shared/lib/format'
import { ArrowRight, CircleCheck, Icon } from '@/shared/ui'
import styles from './ReauditBanner.module.css'

interface ReauditBannerProps {
  revision: number
  blockers: number
  errors: { before: number; after: number }
  stats: { fixed: number; dismissed: number; failed: number }
  exportHref: string
  onClose: () => void
}

function reauditDelta(errors: { before: number; after: number }, fixed: number): string {
  return `ошибок ${errors.before} → ${errors.after} · исправлено ${fixed}`
}

export function ReauditBanner({ revision, blockers, errors, stats, exportHref, onClose }: ReauditBannerProps) {
  const delta = reauditDelta(errors, stats.fixed)
  if (blockers === 0) {
    return (
      <div role="status" className={styles.success}>
        <span className={styles.check} aria-hidden>
          <Icon as={CircleCheck} size={18} />
        </span>
        <div className={styles.text}>
          <div className={styles.successTitle}>Повторный аудит: 0 блокеров</div>
          <div className={styles.detail}>
            Ревизия r{revision} · {delta}
            {stats.dismissed > 0 ? `, отклонено с причиной ${stats.dismissed}` : ''}
            {stats.failed > 0 ? `, не удалось ${stats.failed}` : ''}. Всё раскрыто в журнале и паспорте.
          </div>
        </div>
        <Link to={exportHref} className={styles.primary}>
          Скачать PPTX и паспорт
          <Icon as={ArrowRight} size={14} />
        </Link>
        <button type="button" className={styles.close} onClick={onClose}>
          Скрыть
        </button>
      </div>
    )
  }
  return (
    <div role="status" className={styles.partial}>
      <div className={styles.partialTitle}>
        Повторный аудит: {blockers} {pluralize(blockers, ['блокер', 'блокера', 'блокеров'])}
      </div>
      <div className={styles.detail}>
        Ревизия r{revision} · {delta}. Исправьте или отклоните блокеры — иначе это попадёт в паспорт.
      </div>
      <div className={styles.spacer} />
      <button type="button" className={styles.close} onClick={onClose}>
        Понятно
      </button>
    </div>
  )
}
