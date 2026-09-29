import { clsx } from 'clsx'
import { Check, Icon, X } from '@/shared/ui'
import type { ProofCell } from '../model/passportView'
import styles from './EditabilityProof.module.css'

export function EditabilityProof({ cells }: { cells: readonly ProofCell[] }) {
  return (
    <section className={styles.strip} aria-label="Доказательство редактируемости">
      {cells.map((cell) => (
        <div key={cell.key} className={styles.cell} data-testid={`proof-${cell.key}`}>
          <div className={clsx(styles.value, cell.key === 'roundTrip' ? styles.mark : styles.number, styles[cell.tone])}>
            {cell.key === 'roundTrip' && cell.value !== '—' ? <Icon as={cell.value === '✓' ? Check : X} size={28} strokeWidth={2.5} /> : cell.value}
          </div>
          <div className={styles.label}>{cell.label}</div>
          <div className={styles.hint}>{cell.hint}</div>
        </div>
      ))}
    </section>
  )
}
