import { clsx } from 'clsx'
import type { CheckKind } from '../model/types'
import styles from './CheckKindBadge.module.css'

const TITLE: Record<CheckKind, string> = {
  D: 'Детерминированная проверка: правило в коде, результат всегда один',
  N: 'Контекстная проверка: отвечает модель по картинке слайда',
}

export function CheckKindBadge({ kind }: { kind: CheckKind }) {
  return (
    <span title={TITLE[kind]} aria-label={TITLE[kind]} className={clsx(styles.badge, kind === 'D' ? styles.rule : styles.model)}>
      {kind}
    </span>
  )
}
