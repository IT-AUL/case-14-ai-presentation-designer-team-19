import { useState } from 'react'
import type { InsightPatch, Understanding } from '@/entities/assistant'
import { datasetChartWhy, datasetGroups, insightTitle, splitProfile, textBlocks } from '../lib/table'
import { InsightCard } from './InsightCard'
import { ThoughtCard } from './ThoughtCard'
import styles from './DraftDeck.module.css'

export type Selection = { kind: 'insight'; id: string; edit?: boolean } | { kind: 'block'; id: string; edit?: boolean }

const VISIBLE_BLOCKS = 6
const VISIBLE_INSIGHTS = 6

interface DraftDeckProps {
  understanding: Understanding
  selection: Selection | null
  onSelect?: (selection: Selection) => void
  onPatchInsight?: (insightId: string, patch: InsightPatch) => void
  onExcludeInsight?: (insightId: string) => void
  onIncludeInsight?: (insightId: string) => void
  onRemoveBlock?: (blockId: string) => void
  onConfirmAllInsights?: () => void
  confirmAllPending?: boolean
  excludedInsightIds?: readonly string[]
  quiet?: boolean
}

export function DraftDeck({
  understanding,
  selection,
  onSelect,
  onPatchInsight,
  onExcludeInsight,
  onIncludeInsight,
  onRemoveBlock,
  onConfirmAllInsights,
  confirmAllPending,
  excludedInsightIds,
  quiet,
}: DraftDeckProps) {
  const [showAllBlocks, setShowAllBlocks] = useState(false)
  const [showAllInsights, setShowAllInsights] = useState(false)
  const groups = datasetGroups(understanding)
  const blocks = textBlocks(understanding)
  const excluded = new Set(excludedInsightIds ?? [])
  const visibleBlocks = showAllBlocks ? blocks : blocks.slice(0, VISIBLE_BLOCKS)
  const both = groups.length > 0 && blocks.length > 0
  const pendingInsights = groups.flatMap(({ entries }) => entries).filter((entry) => !entry.insight.confirmed && !excluded.has(entry.insight.id)).length

  return (
    <div className={styles.table}>
      {groups.length > 0 && (
        <section className={styles.group}>
          {both && <h2 className={styles.groupTitle}>Из ваших данных</h2>}
          {(groups.length > 1 || (onConfirmAllInsights && pendingInsights > 0)) && (
            <div className={styles.insightBar}>
              {groups.length > 1 && (
                <nav className={styles.datasetNav} aria-label="Таблицы данных">
                  {groups.map(({ dataset, entries }, index) => (
                    <a
                      key={dataset.id}
                      className={styles.datasetChip}
                      href={`#dataset-${index}`}
                      onClick={(event) => {
                        event.preventDefault()
                        document.getElementById(`dataset-${index}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                      }}
                    >
                      {dataset.title ?? dataset.file ?? `Таблица ${index + 1}`}
                      <span className={styles.datasetCount}>{entries.length}</span>
                    </a>
                  ))}
                </nav>
              )}
              {onConfirmAllInsights && pendingInsights > 0 && (
                <button type="button" className={styles.link} disabled={confirmAllPending} onClick={onConfirmAllInsights}>
                  Отметить все выводы ({pendingInsights})
                </button>
              )}
            </div>
          )}
          <div className={styles.insightList} role="list" aria-label="Выводы из данных" data-muted={quiet || undefined}>
            {groups.map(({ dataset, entries }, index) => {
              const active = entries.filter((entry) => !excluded.has(entry.insight.id))
              const hidden = entries.filter((entry) => excluded.has(entry.insight.id))
              const shown = showAllInsights ? active : active.slice(0, VISIBLE_INSIGHTS)
              const profile = splitProfile(dataset.profile_summary)
              const details = [...profile.details, ...(dataset.task_ru ? [dataset.task_ru] : [])]
              const chartWhy = datasetChartWhy(dataset)
              return (
                <div className={styles.dataset} id={`dataset-${index}`} role="group" aria-label={dataset.title ?? dataset.file ?? 'Таблица'} key={dataset.id}>
                  <header className={styles.datasetHead}>
                    <div className={styles.datasetName}>
                      <h3 className={styles.datasetTitle}>{dataset.title ?? dataset.file ?? 'Таблица'}</h3>
                      {dataset.file && dataset.title !== dataset.file && <span className={styles.datasetFile}>{dataset.file}</span>}
                    </div>
                    {profile.basics.length > 0 && (
                      <ul className={styles.datasetProfile} aria-label="Профиль таблицы">
                        {profile.basics.map((fact) => (
                          <li className={styles.datasetFact} key={fact}>
                            {fact}
                          </li>
                        ))}
                      </ul>
                    )}
                    {chartWhy && <p className={styles.datasetWhy}>{chartWhy}</p>}
                    {details.length > 0 && (
                      <details className={styles.datasetMore}>
                        <summary className={styles.datasetMoreToggle}>Что в таблице</summary>
                        <ul className={styles.datasetDetails}>
                          {details.map((fact) => (
                            <li key={fact}>{fact}</li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </header>

                  {shown.map((entry, index) => {
                    const insight = entry.insight
                    return (
                      <InsightCard
                        key={insight.id}
                        entry={entry}
                        selected={selection?.kind === 'insight' && selection.id === insight.id}
                        onOpen={onSelect ? () => onSelect({ kind: 'insight', id: insight.id }) : undefined}
                        onEdit={onSelect ? () => onSelect({ kind: 'insight', id: insight.id, edit: true }) : undefined}
                        onPatch={onPatchInsight}
                        onExclude={onExcludeInsight}
                        showChart={index === 0}
                      />
                    )
                  })}

                  {active.length > shown.length && (
                    <button type="button" className={styles.link} onClick={() => setShowAllInsights(true)}>
                      Показать все {active.length}
                    </button>
                  )}

                  {hidden.length > 0 && (
                    <div className={styles.excluded} role="group" aria-label="Исключённые выводы">
                      <span className={styles.excludedLabel}>Не включу:</span>
                      {hidden.map((entry) => (
                        <span className={styles.excludedItem} key={entry.insight.id}>
                          <span className={styles.excludedTitle}>{insightTitle(entry.insight)}</span>
                          {onIncludeInsight && (
                            <button type="button" className={styles.link} onClick={() => onIncludeInsight(entry.insight.id)}>
                              Вернуть
                            </button>
                          )}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </section>
      )}

      {blocks.length > 0 && (
        <section className={styles.group}>
          {both && <h2 className={styles.groupTitle}>Из текста</h2>}
          <div className={styles.thoughts} role="list" aria-label="Мысли из текста" data-muted={quiet || undefined}>
            {visibleBlocks.map((block) => (
              <ThoughtCard
                key={block.id}
                block={block}
                datasets={understanding.datasets}
                selected={selection?.kind === 'block' && selection.id === block.id}
                onOpen={onSelect ? () => onSelect({ kind: 'block', id: block.id }) : undefined}
                onEdit={onSelect ? () => onSelect({ kind: 'block', id: block.id, edit: true }) : undefined}
                onRemove={onRemoveBlock}
              />
            ))}
          </div>
          {blocks.length > VISIBLE_BLOCKS && (
            <button type="button" className={styles.link} onClick={() => setShowAllBlocks((value) => !value)}>
              {showAllBlocks ? 'Свернуть' : `Показать все ${blocks.length}`}
            </button>
          )}
        </section>
      )}

      {groups.length === 0 && blocks.length === 0 && <p className={styles.emptyTable}>Материала пока мало. На следующем шаге решим, чем дополнить.</p>}
    </div>
  )
}
