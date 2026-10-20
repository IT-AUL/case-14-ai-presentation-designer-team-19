import { clsx } from 'clsx'
import { Fragment, useMemo, type ReactNode } from 'react'
import type { DeckPlan, ResearchSource, SlidePlan } from '@/entities/generation'
import { pluralize } from '@/shared/lib/format'
import {
  availableQuickActions,
  buildPlanView,
  deckPurposeLabel,
  resolveDeckKeyMessage,
  resolveSufficiencyBanner,
  type PlanRow,
  type QuickActionKind,
  type SufficiencyLike,
} from '../lib/plan'
import styles from './DeckPlanView.module.css'
import { ArrowDown, ArrowRightLeft, ArrowUp, CornerDownRight, Icon, ListOrdered, Minus, Plus, Search, Sparkles, X } from '@/shared/ui'
import { ChartReasoningBox } from './ChartReasoningBox'
import { KeyMessageBanner } from './KeyMessageBanner'
import { SlideContentEditor } from './SlideContentEditor'

export interface PlanEditor {
  onMove: (slideId: string, delta: -1 | 1) => void
  onRemove: (slideId: string) => void
  onUpdate: (slideId: string, patch: { title_intent?: string; key_message?: string; desired_visual?: SlidePlan['desired_visual']; purpose?: SlidePlan['purpose'] }) => void
  onAdd: () => void
  onUnitChange: (slideId: string, position: number, text: string) => void
  onUnitAdd: (slideId: string) => void
  onUnitRemove: (slideId: string, position: number) => void
  onUnitMove: (slideId: string, position: number, delta: -1 | 1) => void
  onChartChange?: (slideId: string, chartType: string) => void
  onQuickAction?: (slideId: string, action: QuickActionKind) => void
  onResearch?: (slideId: string) => void
  researchingSlideId?: string | null
  onKeyMessageChange?: (keyMessage: string) => void
  onKeepOnTopic?: (slideId: string) => void
  canUndo?: boolean
  onUndo?: () => void
}

interface DeckPlanViewProps {
  plan: DeckPlan
  headings: ReadonlyMap<string, string>
  editor?: PlanEditor
  sources?: readonly ResearchSource[]
  asideTop?: ReactNode
  keyMessage?: string | null
  sessionBrief?: Record<string, unknown> | null
  sufficiency?: SufficiencyLike | null
  targetCount?: number | null
  onExpand?: () => void
}

const NO_SOURCES: readonly ResearchSource[] = []

const BAR_TONES = 5
const BOOKEND_PURPOSES = new Set(['title', 'thank_you', 'cta', 'agenda', 'closing'])

function plannerText(planner: string): string {
  if (!planner) return 'По структуре материалов'
  if (planner.startsWith('data_story')) return 'По данным и тезисам'
  if (planner === 'deterministic' || planner === 'heuristic') return 'По правилам структуры'
  return /[а-яё]/i.test(planner) ? planner : 'По структуре материалов'
}

function modeText(mode: string | null | undefined): string {
  if (!mode || mode === 'deterministic' || mode === 'none' || mode === 'heuristic') return 'По правилам'
  return /[а-яё]/i.test(mode) ? mode : 'Расширенный разбор'
}

interface PlanRowItemProps {
  row: PlanRow
  slide?: SlidePlan
  quickActions: readonly QuickActionKind[]
  sources: ReadonlyMap<string, ResearchSource>
  first: boolean
  last: boolean
  single: boolean
  editor?: PlanEditor
}

function QuickActionsToolbar({ row, slide, quickActions, editor }: Pick<PlanRowItemProps, 'row' | 'slide' | 'quickActions' | 'editor'>) {
  if (!editor) return null
  const trigger = (action: QuickActionKind) => {
    if (editor.onQuickAction) editor.onQuickAction(row.id, action)
    else if (action === 'remove') editor.onRemove(row.id)
    else if (action === 'to_chart') editor.onUpdate(row.id, { desired_visual: 'chart' })
    else if (action === 'to_diagram') editor.onUpdate(row.id, { desired_visual: 'diagram' })
  }
  const canResearch = Boolean(editor.onResearch && slide && !BOOKEND_PURPOSES.has(slide.purpose))
  const visibleActions = quickActions.filter((action) => action !== 'remove')
  if (visibleActions.length === 0 && !canResearch) return null

  return (
    <div className={styles.quickToolbar} role="toolbar" aria-label={`Быстрые действия со слайдом ${row.number}`}>
      {visibleActions.includes('shorten') && (
        <button
          type="button"
          className={clsx(styles.quickButton, styles.live)}
          aria-label={`Короче слайд ${row.number}`}
          title="Сделать пункты короче"
          onClick={() => trigger('shorten')}
        >
          <Icon as={Minus} size={12} />
          <span>Короче</span>
        </button>
      )}
      {visibleActions.includes('split') && (
        <button
          type="button"
          className={clsx(styles.quickButton, styles.live)}
          aria-label={`Разбить на два слайд ${row.number}`}
          title="Разбить слайд на два"
          onClick={() => trigger('split')}
        >
          <Icon as={ArrowRightLeft} size={12} />
          <span>Разбить на два</span>
        </button>
      )}
      {visibleActions.includes('merge_next') && (
        <button
          type="button"
          className={clsx(styles.quickButton, styles.live)}
          aria-label={`Объединить со следующим слайд ${row.number}`}
          title="Объединить со следующим слайдом"
          onClick={() => trigger('merge_next')}
        >
          <Icon as={CornerDownRight} size={12} />
          <span>Объединить со следующим</span>
        </button>
      )}
      {canResearch && (
        <button
          type="button"
          className={clsx(styles.quickButton, styles.live)}
          aria-label={`Найти цифры для слайда ${row.number}`}
          title="Найти цифры в источниках"
          disabled={editor.researchingSlideId === row.id}
          onClick={() => editor.onResearch?.(row.id)}
        >
          <Icon as={Search} size={12} />
          <span>{editor.researchingSlideId === row.id ? 'Ищу цифры…' : 'Найти цифры'}</span>
        </button>
      )}
      {visibleActions.includes('to_chart') && (
        <button
          type="button"
          className={clsx(styles.quickButton, styles.live)}
          aria-label={`Сделать графиком слайд ${row.number}`}
          title="Оформить слайд графиком"
          onClick={() => trigger('to_chart')}
        >
          <Icon as={Sparkles} size={12} />
          <span>Сделать графиком</span>
        </button>
      )}
      {visibleActions.includes('to_diagram') && (
        <button
          type="button"
          className={clsx(styles.quickButton, styles.live)}
          aria-label={`Сделать схемой слайд ${row.number}`}
          title="Оформить пункты схемой"
          onClick={() => trigger('to_diagram')}
        >
          <Icon as={ListOrdered} size={12} />
          <span>Сделать схемой</span>
        </button>
      )}
    </div>
  )
}

function RowActions({ row, slide, first, last, single, editor }: PlanRowItemProps) {
  if (!editor) return <div className={styles.rowActions} />
  const cannotRemove = single || (Boolean(editor.onQuickAction) && slide?.purpose === 'title')
  const handleRemove = () => {
    if (editor.onQuickAction) editor.onQuickAction(row.id, 'remove')
    else editor.onRemove(row.id)
  }
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
        disabled={cannotRemove}
        onClick={handleRemove}
      >
        <Icon as={X} size={14} />
      </button>
    </div>
  )
}

function RowMain({ row, slide, quickActions, sources, single, editor }: Pick<PlanRowItemProps, 'row' | 'slide' | 'quickActions' | 'sources' | 'single' | 'editor'>) {
  const offTopic = row.supportsKeyMessage === false
  const handleRemove = () => {
    if (!editor) return
    if (editor.onQuickAction) editor.onQuickAction(row.id, 'remove')
    else editor.onRemove(row.id)
  }

  if (!editor) {
    return (
      <div className={styles.main}>
        {offTopic && (
          <div className={styles.offTopicBar} role="note">
            <span className={styles.offTopicLabel}>Не про главную мысль — убрать?</span>
          </div>
        )}
        <div className={styles.title}>{row.title}</div>
        {(row.idea || row.visual) && (
          <div className={styles.idea}>
            {row.idea}
            {row.idea && row.visual && ' · '}
            {row.visual && <span className={styles.visual}>{row.visual}</span>}
          </div>
        )}
        {row.chart && <ChartReasoningBox slideId={row.id} number={row.number} chart={row.chart} />}
      </div>
    )
  }
  return (
    <div className={styles.main}>
      {offTopic && (
        <div className={styles.offTopicBar} role="note">
          <span className={styles.offTopicLabel}>Не про главную мысль — убрать?</span>
          <span className={styles.offTopicActions}>
            <button
              type="button"
              className={clsx(styles.offTopicBtn, styles.offTopicBtnPrimary, styles.live)}
              aria-label={`Убрать слайд ${row.number} вне главной мысли`}
              disabled={single || slide?.purpose === 'title'}
              onClick={handleRemove}
            >
              Убрать
            </button>
            {editor.onKeepOnTopic && (
              <button type="button" className={clsx(styles.offTopicBtn, styles.live)} onClick={() => editor.onKeepOnTopic?.(row.id)}>
                Оставить
              </button>
            )}
          </span>
        </div>
      )}
      <div className={styles.titleHeader}>
        <input
          className={styles.titleInput}
          value={row.titleIntent}
          placeholder="Заголовок-вывод"
          aria-label={`Заголовок слайда ${row.number}`}
          onChange={(event) => editor.onUpdate(row.id, { title_intent: event.target.value })}
        />
        <QuickActionsToolbar row={row} slide={slide} quickActions={quickActions} editor={editor} />
      </div>
      <textarea
        className={styles.ideaInput}
        value={row.keyMessage}
        rows={2}
        placeholder="Главная мысль слайда"
        aria-label={`Мысль слайда ${row.number}`}
        onChange={(event) => editor.onUpdate(row.id, { key_message: event.target.value })}
      />
      {row.chart && <ChartReasoningBox slideId={row.id} number={row.number} chart={row.chart} onSelectChart={editor.onChartChange} />}
      {slide ? <SlideContentEditor slide={slide} number={row.number} editor={editor} sources={sources} /> : row.visual && <span className={styles.visual}>{row.visual}</span>}
    </div>
  )
}

function PlanRowItem({ row, slide, quickActions, sources, first, last, single, editor }: PlanRowItemProps) {
  const offTopic = row.supportsKeyMessage === false
  return (
    <li className={clsx(styles.row, offTopic && styles.offTopic)} data-off-topic={offTopic || undefined}>
      <div className={styles.number}>{row.number}</div>
      <div className={styles.purposeCell}>
        <span className={styles.purpose}>{row.purpose}</span>
      </div>
      <RowMain row={row} slide={slide} quickActions={quickActions} sources={sources} single={single} editor={editor} />
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
      <RowActions row={row} slide={slide} quickActions={quickActions} sources={sources} first={first} last={last} single={single} editor={editor} />
    </li>
  )
}

export function DeckPlanView({
  plan,
  headings,
  editor,
  sources = NO_SOURCES,
  asideTop,
  keyMessage,
  sessionBrief,
  sufficiency,
  targetCount,
  onExpand,
}: DeckPlanViewProps) {
  const view = useMemo(() => buildPlanView(plan, headings), [plan, headings])
  const slidesById = useMemo(() => new Map(plan.slides.map((slide) => [slide.id, slide])), [plan.slides])
  const sourcesById = useMemo(() => new Map(sources.map((source) => [source.id, source])), [sources])
  const { rows, sections, stats } = view
  const { provenance, brief } = plan
  const namedSections = sections.filter((section) => section.id !== 'unassigned').length
  const audience = (plan.audience || brief.audience || '').trim()
  const resolvedKeyMessage = resolveDeckKeyMessage(plan, keyMessage, sessionBrief)
  const showKeyMessage = Boolean(editor?.onKeyMessageChange || keyMessage || brief.key_message || sessionBrief)
  const sufficiencyBanner = resolveSufficiencyBanner(sufficiency, targetCount ?? brief.target_slide_count)

  return (
    <div className={styles.planWrapper}>
      {showKeyMessage && (
        <KeyMessageBanner
          key={resolvedKeyMessage}
          keyMessage={resolvedKeyMessage}
          onSave={editor?.onKeyMessageChange}
          canUndo={editor?.canUndo}
          onUndo={editor?.onUndo}
        />
      )}
      {sufficiencyBanner && (
        <div className={styles.sufficiencyBanner} role="status">
          <span className={styles.sufficiencyText}>{sufficiencyBanner.text}</span>
          {sufficiencyBanner.canExpand && (
            <button type="button" className={clsx(styles.sufficiencyAction, styles.live)} onClick={onExpand}>
              Дополнить
            </button>
          )}
        </div>
      )}
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
                  <PlanRowItem
                    row={row}
                    slide={slidesById.get(row.id)}
                    quickActions={editor ? availableQuickActions(plan, row.id) : []}
                    sources={sourcesById}
                    first={position === 0}
                    last={position === rows.length - 1}
                    single={rows.length === 1}
                    editor={editor}
                  />
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
        {asideTop}
        <h2 className={styles.asideTitle}>Почему такая структура</h2>
        <p className={styles.asideText}>
          Назначение «{deckPurposeLabel(brief.purpose || plan.objective)}»
          {audience ? `, аудитория «${audience}»` : ''}.{' '}
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
              <dd>{plannerText(provenance.planner)}</dd>
            </div>
            <div>
              <dt>Режим</dt>
              <dd>{modeText(provenance.model_id)}</dd>
            </div>
            <div>
              <dt>Версия правил</dt>
              <dd>{provenance.prompt_version}</dd>
            </div>
          </dl>
        </div>
      </aside>
      </div>
    </div>
  )
}
