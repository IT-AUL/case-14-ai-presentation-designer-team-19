import { clsx } from 'clsx'
import { Fragment, useMemo } from 'react'
import type { DeckPlan } from '@/entities/generation'
import { pluralize } from '@/shared/lib/format'
import { buildPlanView, deckPurposeLabel, type PlanRow } from '../lib/plan'
import styles from './DeckPlanView.module.css'
import { ArrowDown, ArrowUp, CornerDownRight, Icon, Plus, X } from '@/shared/ui'

export interface PlanEditor {
  onMove: (slideId: string, delta: -1 | 1) => void
  onRemove: (slideId: string) => void
  onUpdate: (slideId: string, patch: { title_intent?: string; key_message?: string }) => void
  onAdd: () => void
}

interface DeckPlanViewProps {
  plan: DeckPlan
  headings: ReadonlyMap<string, string>
  editor?: PlanEditor
}

const BAR_TONES = 5

interface PlanRowItemProps {
  row: PlanRow
  first: boolean
  last: boolean
  single: boolean
  editor?: PlanEditor
}

function RowActions({ row, first, last, single, editor }: PlanRowItemProps) {
  if (!editor) return <div className={styles.rowActions} />
  return (
    <div className={styles.rowActions}>
      <button type="button" className={clsx(styles.iconButton, styles.live)} aria-label={`Поднять слайд ${row.number} выше`} disabled={first} onClick={() => editor.onMove(row.id, -1)}>
        <Icon as={ArrowUp} size={14} />
      </button>
      <button type="button" className={clsx(styles.iconButton, styles.live)} aria-label={`Опустить слайд ${row.number} ниже`} disabled={last} onClick={() => editor.onMove(row.id, 1)}>
        <Icon as={ArrowDown} size={14} />
      </button>
      <button
        type="button"
        className={clsx(styles.iconButton, styles.live, styles.remove)}
        aria-label={`Убрать слайд ${row.number}`}
        disabled={single}
        onClick={() => editor.onRemove(row.id)}
      >
        <Icon as={X} size={14} />
      </button>
    </div>
  )
}

function RowMain({ row, editor }: Pick<PlanRowItemProps, 'row' | 'editor'>) {
  if (!editor) {
    return (
      <div className={styles.main}>
        <div className={styles.title}>{row.title}</div>
        {(row.idea || row.visual) && (
          <div className={styles.idea}>
            {row.idea}
            {row.idea && row.visual && ' · '}
            {row.visual && <span className={styles.visual}>{row.visual}</span>}
          </div>
        )}
      </div>
    )
  }
  return (
    <div className={styles.main}>
      <input
        className={styles.titleInput}
        value={row.titleIntent}
        placeholder="Заголовок-вывод"
        aria-label={`Заголовок слайда ${row.number}`}
        onChange={(event) => editor.onUpdate(row.id, { title_intent: event.target.value })}
      />
      <textarea
        className={styles.ideaInput}
        value={row.keyMessage}
        rows={2}
        placeholder="Главная мысль слайда"
        aria-label={`Мысль слайда ${row.number}`}
        onChange={(event) => editor.onUpdate(row.id, { key_message: event.target.value })}
      />
      {row.visual && <span className={styles.visual}>{row.visual}</span>}
    </div>
  )
}

function PlanRowItem({ row, first, last, single, editor }: PlanRowItemProps) {
  return (
    <li className={styles.row}>
      <div className={styles.number}>{row.number}</div>
      <div className={styles.purposeCell}>
        <span className={styles.purpose}>{row.purpose}</span>
      </div>
      <RowMain row={row} editor={editor} />
      <div className={styles.sources}>
        {row.sources.length > 0 ? (
          row.sources.map((source) => (
            <span key={source.key} className={styles.source} title={`${source.label}\n${source.refs.join('\n')}`}>
              <Icon as={CornerDownRight} size={12} /> {source.label}
            </span>
          ))
        ) : (
          <span className={styles.noSource}>служебный слайд</span>
        )}
      </div>
      <RowActions row={row} first={first} last={last} single={single} editor={editor} />
    </li>
  )
}

export function DeckPlanView({ plan, headings, editor }: DeckPlanViewProps) {
  const view = useMemo(() => buildPlanView(plan, headings), [plan, headings])
  const { rows, sections, stats } = view
  const { provenance, brief } = plan
  const namedSections = sections.filter((section) => section.id !== 'unassigned').length

  return (
    <div className={styles.layout}>
      <div className={styles.listColumn}>
        <div className={styles.columns} aria-hidden>
          <div>№</div>
          <div>Назначение</div>
          <div>Заголовок-вывод и мысль</div>
          <div>Источник</div>
          <div />
        </div>
        <ol className={styles.list} aria-label="Слайды плана">
          {rows.map((row, position) => {
            const previous = rows[position - 1]
            const opensSection = row.sectionTitle !== null && row.sectionId !== previous?.sectionId
            return (
              <Fragment key={row.id}>
                {opensSection && (
                  <li className={styles.sectionHead}>
                    Раздел · {row.sectionTitle}
                  </li>
                )}
                <PlanRowItem row={row} first={position === 0} last={position === rows.length - 1} single={rows.length === 1} editor={editor} />
              </Fragment>
            )
          })}
        </ol>
        {editor && (
          <button type="button" className={clsx(styles.addButton, styles.live)} onClick={editor.onAdd}>
            <Icon as={Plus} size={14} /> Добавить слайд
          </button>
        )}
      </div>

      <aside className={styles.aside} aria-label="Почему такая структура">
        <h2 className={styles.asideTitle}>Почему такая структура</h2>
        <p className={styles.asideText}>
          Назначение «{deckPurposeLabel(brief.purpose || plan.objective)}», аудитория «{plan.audience || brief.audience}».{' '}
          {namedSections > 0
            ? `План делит историю на ${namedSections} ${pluralize(namedSections, ['раздел', 'раздела', 'разделов'])}; у каждого — слайды, которые его раскрывают.`
            : ''}
        </p>
        {sections.length > 0 && (
          <>
            <div className={styles.bar} aria-hidden>
              {sections.map((section, index) => (
                <div key={section.id} className={styles[`tone${index % BAR_TONES}`]} style={{ flex: section.count }} />
              ))}
            </div>
            <ul className={styles.sectionList}>
              {sections.map((section) => (
                <li key={section.id} className={styles.sectionItem}>
                  <span className={styles.sectionName} title={section.title}>
                    {section.title}
                  </span>
                  <span className={styles.sectionRange}>{section.range}</span>
                </li>
              ))}
            </ul>
          </>
        )}
        <dl className={styles.stats}>
          <div className={styles.stat}>
            <dt>Слайдов</dt>
            <dd className={styles.mono}>{stats.slides}</dd>
          </div>
          <div className={styles.stat}>
            <dt>С источником</dt>
            <dd className={styles.mono}>
              {stats.withSources} из {stats.slides}
            </dd>
          </div>
          <div className={styles.stat}>
            <dt>С визуализацией</dt>
            <dd className={styles.mono}>{stats.withVisual}</dd>
          </div>
          {(stats.mandatory > 0 || (brief.mandatory_sections?.length ?? 0) > 0) && (
            <div className={styles.stat}>
              <dt>Обязательные разделы</dt>
              <dd className={styles.mono}>
                {stats.mandatory} {pluralize(stats.mandatory, ['слайд', 'слайда', 'слайдов'])} · в брифе {brief.mandatory_sections?.length ?? 0}
              </dd>
            </div>
          )}
        </dl>
        <div className={styles.provenance}>
          <div className={styles.provenanceTitle}>Происхождение плана</div>
          <dl className={styles.provenanceList}>
            <div>
              <dt>Планировщик</dt>
              <dd>{provenance.planner}</dd>
            </div>
            <div>
              <dt>Модель</dt>
              <dd>{provenance.model_id ?? 'не указана'}</dd>
            </div>
            <div>
              <dt>Промпт</dt>
              <dd>{provenance.prompt_version}</dd>
            </div>

          </dl>
        </div>
      </aside>
    </div>
  )
}
