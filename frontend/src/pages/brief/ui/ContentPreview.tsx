import { useId } from 'react'
import { summarizeContentPack, useContentPack } from '@/entities/content-pack'
import type { ContentPack } from '@/entities/content-pack'
import { formatNumber, pluralize } from '@/shared/lib/format'
import { Skeleton } from '@/shared/ui'
import styles from './ContentPreview.module.css'

export interface ContentPreviewProps {
  packId: string | null
  sourceLabel: string | null
  parsing?: boolean
  stale?: boolean
  error?: string | null
}

const SECTION_LIMIT = 8

function Stat({ value, forms }: { value: number; forms: readonly [string, string, string] }) {
  return (
    <div className={styles.stat}>
      <span className={styles.statValue}>{formatNumber(value)}</span>
      <span className={styles.statLabel}>{pluralize(value, forms)}</span>
    </div>
  )
}

function tableTitle(title: string | null | undefined, index: number): string {
  return title?.trim() ? `Табл. ${index + 1} · ${title.trim()}` : `Табл. ${index + 1}`
}

function PackDetails({ pack, sourceLabel }: { pack: ContentPack; sourceLabel: string | null }) {
  const summary = summarizeContentPack(pack)
  const sections = summary.sections.slice(0, SECTION_LIMIT)
  const hiddenSections = summary.sections.length - sections.length
  const words = `${formatNumber(summary.words)} ${pluralize(summary.words, ['слово', 'слова', 'слов'])}`

  return (
    <>
      <div className={styles.subtitle}>{[sourceLabel, words].filter(Boolean).join(' · ')}</div>
      <div className={styles.stats}>
        <Stat value={summary.sections.length} forms={['раздел', 'раздела', 'разделов']} />
        <Stat value={summary.tables} forms={['таблица', 'таблицы', 'таблиц']} />
        <Stat value={summary.numbers} forms={['число', 'числа', 'чисел']} />
        <Stat value={summary.diagrams} forms={['схема', 'схемы', 'схем']} />
      </div>
      {sections.length > 0 && (
        <ol className={styles.sections} aria-label="Разделы контента">
          {sections.map((section, index) => (
            <li key={section.id} className={styles.sectionRow}>
              <span className={styles.sectionIndex}>§{index + 1}</span>
              <span className={styles.sectionHeading} data-level={section.level > 1 ? 'sub' : 'top'}>
                {section.heading}
              </span>
            </li>
          ))}
          {hiddenSections > 0 && (
            <li className={styles.sectionMore}>
              и ещё {hiddenSections} {pluralize(hiddenSections, ['раздел', 'раздела', 'разделов'])}
            </li>
          )}
        </ol>
      )}
      {(pack.tables.length > 0 || pack.charts.length > 0) && (
        <div className={styles.group}>
          <div className={styles.groupTitle}>Данные для графиков и таблиц</div>
          {pack.tables.map((table, index) => (
            <div key={table.id} className={styles.dataRow}>
              <span>{tableTitle(table.title, index)}</span>
              <span className={styles.dataMeta}>
                {table.rows.length} × {table.headers.length}
              </span>
            </div>
          ))}
          {pack.charts.map((chart, index) => (
            <div key={chart.id} className={styles.dataRow}>
              <span>Ряд данных {index + 1}</span>
              <span className={styles.dataMeta}>
                {chart.series.length} {pluralize(chart.series.length, ['серия', 'серии', 'серий'])} · {chart.categories.length}{' '}
                {pluralize(chart.categories.length, ['категория', 'категории', 'категорий'])}
              </span>
            </div>
          ))}
        </div>
      )}
      {summary.warnings.length > 0 && (
        <ul className={styles.warnings} aria-label="Предупреждения разбора">
          {summary.warnings.map((warning) => (
            <li key={warning}>{warning}</li>
          ))}
        </ul>
      )}
    </>
  )
}

export function ContentPreview({ packId, sourceLabel, parsing = false, stale = false, error = null }: ContentPreviewProps) {
  const titleId = useId()
  const { data: pack, isPending, error: loadError } = useContentPack(packId)
  const loading = parsing || (Boolean(packId) && isPending)

  return (
    <section className={styles.card} aria-labelledby={titleId} aria-busy={loading}>
      <h2 id={titleId} className={styles.title}>
        Что система поняла из контента
      </h2>
      {loading ? (
        <div className={styles.loading}>
          <Skeleton className={styles.skeletonLine} />
          <div className={styles.stats}>
            {[0, 1, 2, 3].map((index) => (
              <Skeleton key={index} className={styles.skeletonStat} delay={index * 0.1} />
            ))}
          </div>
          <Skeleton className={styles.skeletonBlock} delay={0.2} />
        </div>
      ) : error ? (
        <p className={styles.error} role="alert">
          {error}
        </p>
      ) : !packId ? (
        <p className={styles.empty}>Здесь появятся разделы, таблицы и числа из контента.</p>
      ) : loadError || !pack ? (
        <p className={styles.error} role="alert">
          Не удалось загрузить разбор контента{loadError ? `: ${loadError.message}` : ''}
        </p>
      ) : (
        <PackDetails pack={pack} sourceLabel={sourceLabel} />
      )}
      {stale && !loading && <p className={styles.stale}>Контент изменился — разберём его заново при сборке вариантов.</p>}
    </section>
  )
}
