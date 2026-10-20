import { Badge } from '@/shared/ui'
import type { DeckSpecModel } from '../lib/questions'
import styles from './QuestionsPage.module.css'

interface DeckSpecProps {
  spec: DeckSpecModel
}

export function DeckSpec({ spec }: DeckSpecProps) {
  return (
    <div className={styles.specBoard}>
      <section className={styles.specSheet} aria-label="Спецификация будущей колоды">
        <header className={styles.specHeader}>
          <div className={styles.specHeaderMain}>
            <span className={styles.specEyebrow}>Спецификация будущей колоды</span>
            <h2 className={styles.specTitle}>{spec.topic}</h2>
          </div>
          <div className={styles.specHeaderBadges}>
            <Badge tone="ink" shape="tag" mono>
              {spec.slideCountLabel}
            </Badge>
            {spec.capacityLabel && (
              <Badge tone="neutral" shape="tag">
                {spec.capacityLabel}
              </Badge>
            )}
            {spec.datasetLabel && (
              <Badge tone="info" shape="tag">
                {spec.datasetLabel}
              </Badge>
            )}
          </div>
        </header>

        <div className={styles.specGrid} role="list" aria-label="Параметры будущей колоды">
          {spec.items.map((item) => (
            <div key={item.id} role="listitem" className={styles.specCell}>
              <article className={styles.specCard} data-changed={item.changed || undefined}>
                <div className={styles.specCardTop}>
                  <span className={styles.specCardLabel}>{item.label}</span>
                  {item.badge && (
                    <Badge tone={item.changed ? 'ok' : 'neutral'} shape="tag">
                      {item.badge}
                    </Badge>
                  )}
                </div>
                <span className={styles.specCardValue}>{item.value}</span>
                <span className={styles.specCardNote}>{item.note}</span>
              </article>
            </div>
          ))}
        </div>

        <div className={styles.outlineSection}>
          <div className={styles.outlineHeader}>
            <h3 className={styles.outlineHeading}>Как ответы ложатся в структуру плана</h3>
            <span className={styles.outlineSub}>{spec.fillModeLabel}</span>
          </div>
          <ol className={styles.outlineList} aria-label="Ориентир структуры колоды">
            {spec.outline.map((row) => (
              <li key={row.slot} className={styles.outlineRow}>
                <div className={styles.outlineSlotCol}>
                  <span className={styles.outlineSlot}>{row.slot}</span>
                  <span className={styles.outlineRole}>{row.role}</span>
                </div>
                <div className={styles.outlineContentCol}>
                  <span className={styles.outlineTitle}>{row.title}</span>
                  <span className={styles.outlineDetail}>{row.detail}</span>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </section>
    </div>
  )
}
