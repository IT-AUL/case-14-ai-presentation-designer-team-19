import { journalActionText, ruleMeta, slideNumber, type JournalEntry, type RepairBatch } from '@/entities/audit'
import { Check, Icon, type IconComponent, Minus, Mono, X } from '@/shared/ui'
import styles from './IssuePanel.module.css'

const MARK: Record<JournalEntry['outcome'], IconComponent> = { fixed: Check, unresolved: X, dismissed: Minus }

interface JournalListProps {
  entries: readonly JournalEntry[]
  batches: readonly RepairBatch[]
}

function batchSummary(batch: RepairBatch): string {
  const service = batch.serviceCounts
  const serviceText = service ? ` · сервис: применено ${service.applied ?? 0}, пропущено ${service.skipped ?? 0}, не удалось ${(service.failed ?? 0) + (service.unresolved ?? 0)}` : ''
  return `r${batch.fromRevision} → r${batch.revision}: выбрано ${batch.requested}, исправлено ${batch.fixed}, не удалось ${batch.unresolved}${serviceText}`
}

export function JournalList({ entries, batches }: JournalListProps) {
  if (entries.length === 0) {
    return (
      <div className={styles.journal}>
        <div className={styles.empty}>Исправлений и отклонений пока нет.</div>
      </div>
    )
  }

  const newestFirst = [...entries].reverse()
  const newestBatches = [...batches].reverse()

  return (
    <div className={styles.journal}>
      {newestBatches.length > 0 && (
        <ul className={styles.batches} aria-label="Ревизии">
          {newestBatches.map((batch) => (
            <li key={batch.id}>
              <Mono>{batchSummary(batch)}</Mono>
            </li>
          ))}
        </ul>
      )}
      <ul className={styles.entries} aria-label="Журнал изменений">
        {newestFirst.map((entry) => {
          const number = slideNumber(entry.issue)
          return (
            <li key={entry.id} className={styles.entry}>
              <span className={styles.entryMark} data-outcome={entry.outcome} aria-hidden>
                <Icon as={MARK[entry.outcome]} size={12} strokeWidth={3} />
              </span>
              <div className={styles.entryText}>
                <div className={styles.entryTitle}>
                  {ruleMeta(entry.issue.rule_code).name} · {number ? `сл. ${String(number).padStart(2, '0')}` : 'колода'}
                </div>
                <div className={styles.entryAction}>{journalActionText(entry)}</div>
                <Mono className={styles.entryRule}>{entry.issue.rule_code}</Mono>
              </div>
              <Mono className={styles.revision}>r{entry.revision}</Mono>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
