import { useState, type ReactNode } from 'react'
import { journalActionText, type IssueView, type JournalEntry } from '@/entities/audit'
import { AlertTriangle, Check, ChevronLeft, ChevronRight, Icon, type IconComponent, Minus, Mono, PdfPage, Segmented, X } from '@/shared/ui'
import { IssueOverlay, type OverlayItem } from './IssueOverlay'
import styles from './SlideInspector.module.css'

export type InspectorMode = 'slide' | 'compare'

export interface SlideInspectorProps {
  pdfUrl: string | null
  pdfRevision: number | null
  imageUrl: string | null
  imageRevision: number | null
  revision: number
  slideNumber: number
  slideCount: number
  title: string | undefined
  views: readonly IssueView[]
  journal: readonly JournalEntry[]
  activeKey: string | null
  linkedKeys?: readonly string[]
  hoverKey: string | null
  mode: InspectorMode
  onModeChange: (mode: InspectorMode) => void
  onPick: (key: string) => void
  onHover: (key: string | null) => void
  onSlideChange: (slideNumber: number) => void
}

const MODE_OPTIONS = [
  { value: 'slide', label: 'Слайд' },
  { value: 'compare', label: 'До / после' },
] as const

const LEGEND = [
  { key: 'blocker', label: 'блокер' },
  { key: 'error', label: 'ошибка' },
  { key: 'warning', label: 'предупреждение' },
  { key: 'info', label: 'инфо' },
  { key: 'fixed', label: 'исправлено' },
] as const

const MARK: Record<JournalEntry['outcome'], IconComponent> = { fixed: Check, unresolved: X, dismissed: Minus }

const pad = (value: number) => String(value).padStart(2, '0')

interface SlideFrameProps {
  pdfUrl: string | null
  imageUrl?: string | null
  slideNumber: number
  children?: ReactNode
}

function SlideFrame({ pdfUrl, imageUrl, slideNumber, children }: SlideFrameProps) {
  if (pdfUrl || imageUrl) {
    return (
      <PdfPage url={pdfUrl} imageUrl={imageUrl} pageNumber={slideNumber} lazy={false} label={`Слайд ${slideNumber}`}>
        {children}
      </PdfPage>
    )
  }
  return (
    <div className={styles.blank} role="img" aria-label={`Слайд ${slideNumber}: превью нет, показаны только рамки`}>
      <span className={styles.blankNote}>PDF этой ревизии нет — рамки на схеме слайда</span>
      {children}
    </div>
  )
}

export function SlideInspector(props: SlideInspectorProps) {
  const { pdfUrl, pdfRevision, imageUrl, imageRevision, revision, slideNumber, slideCount, title, views, journal, activeKey, hoverKey, mode } = props
  const [showAll, setShowAll] = useState(true)
  const renderRevision = imageUrl ? imageRevision : pdfRevision
  const staleRender = renderRevision !== null && renderRevision < revision

  return (
    <div className={styles.root}>
      <div className={styles.toolbar}>
        <button
          type="button"
          className={styles.nav}
          aria-label="Предыдущий слайд"
          disabled={slideNumber <= 1}
          onClick={() => props.onSlideChange(slideNumber - 1)}
        >
          <Icon as={ChevronLeft} />
        </button>
        <Mono className={styles.counter} aria-live="polite">
          {pad(slideNumber)} / {pad(slideCount)}
        </Mono>
        <button
          type="button"
          className={styles.nav}
          aria-label="Следующий слайд"
          disabled={slideNumber >= slideCount}
          onClick={() => props.onSlideChange(slideNumber + 1)}
        >
          <Icon as={ChevronRight} />
        </button>
        <div className={styles.title} title={title}>
          {title}
        </div>
        <Segmented size="sm" label="Вид слайда" options={MODE_OPTIONS} value={mode} onChange={props.onModeChange} />
      </div>

      {mode === 'slide' ? (
        <>
          <div className={styles.stage}>
            <SlideFrame pdfUrl={pdfUrl} imageUrl={imageUrl} slideNumber={slideNumber}>
              <IssueOverlay
                items={views}
                activeKey={activeKey}
                linkedKeys={props.linkedKeys}
                hoverKey={hoverKey}
                stale={staleRender}
                showAll={showAll}
                onPick={props.onPick}
                onHover={props.onHover}
              />
            </SlideFrame>
            {staleRender && (
              <p className={styles.staleChip} role="note">
                <Icon as={AlertTriangle} size={14} />
                Превью устарело: картинка r{renderRevision}, проблемы r{revision}
              </p>
            )}
          </div>
          <div className={styles.legend}>
            <button type="button" className={styles.frameToggle} aria-pressed={showAll} onClick={() => setShowAll((current) => !current)}>
              <span className={styles.frameToggleBox} aria-hidden>
                {showAll && <Icon as={Check} size={10} strokeWidth={3} />}
              </span>
              Все рамки
            </button>
            {LEGEND.map((item) => (
              <span key={item.key} className={styles.legendItem}>
                <span className={styles.swatch} data-kind={item.key} aria-hidden />
                {item.label}
              </span>
            ))}
            <span className={styles.legendHint}>j / k — проблемы · ← → — слайды</span>
          </div>
          {staleRender && (
            <p className={styles.stale}>
              Сервис ещё не перерисовал превью после правок: рамки показывают проблемы ревизии r{revision} поверх картинки r{renderRevision}.
            </p>
          )}
        </>
      ) : (
        <CompareView
          pdfUrl={pdfUrl}
          pdfRevision={pdfRevision}
          afterImageUrl={imageUrl && imageRevision !== null && imageRevision > (pdfRevision ?? 0) ? imageUrl : null}
          revision={revision}
          slideNumber={slideNumber}
          journal={journal}
        />
      )}
    </div>
  )
}

interface CompareViewProps {
  pdfUrl: string | null
  pdfRevision: number | null
  afterImageUrl: string | null
  revision: number
  slideNumber: number
  journal: readonly JournalEntry[]
}

function CompareView({ pdfUrl, pdfRevision, afterImageUrl, revision, slideNumber, journal }: CompareViewProps) {
  const before: OverlayItem[] = journal.map((entry) => ({ key: entry.id, issue: entry.issue, status: 'open' }))
  const firstRevision = journal.reduce((min, entry) => Math.min(min, entry.revision - 1), revision)
  const beforeRevision = pdfRevision ?? Math.max(1, firstRevision)

  return (
    <div className={styles.compare}>
      <div className={styles.column}>
        <div className={styles.columnTitle}>До · r{beforeRevision}</div>
        <div className={styles.compareStage}>
          <SlideFrame pdfUrl={pdfUrl} slideNumber={slideNumber}>
            <IssueOverlay items={before} />
          </SlideFrame>
        </div>
      </div>
      <div className={styles.column}>
        <div className={styles.columnTitle}>После · r{revision} · что изменено</div>
        {afterImageUrl && (
          <div className={styles.compareStage}>
            <SlideFrame pdfUrl={null} imageUrl={afterImageUrl} slideNumber={slideNumber} />
          </div>
        )}
        <div className={styles.changes}>
          {journal.length === 0 ? (
            <div className={styles.muted}>На этом слайде ничего не менялось.</div>
          ) : (
            <ul className={styles.changeList}>
              {journal.map((entry) => (
                <li key={entry.id} className={styles.change}>
                  <span className={styles.mark} data-outcome={entry.outcome} aria-hidden>
                    <Icon as={MARK[entry.outcome]} size={12} strokeWidth={3} />
                  </span>
                  <div className={styles.changeText}>
                    <div>{journalActionText(entry)}</div>
                    <Mono className={styles.changeMeta}>
                      {entry.issue.rule_code} · r{entry.revision}
                    </Mono>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}
