import type { JobEvent } from '@/entities/assistant'
import {
  formatFactsByOriginText,
  formatShortageExplanationText,
  type ContentFillSummary,
  type DeckPlan,
  type GenerationTracker,
} from '@/entities/generation'
import { useSlideChangeQueue } from '@/features/slide-change'
import { formatIndex } from '@/shared/lib/format'
import { Button, SlideGrid, SlideTile } from '@/shared/ui'
import { buildStageSummary, type BuildStageSummary, type FilmstripSlot } from '../lib/filmstrip'
import type { VariantCard, VariantCardState } from '../lib/variants'
import { useBuildSlots } from '../model/buildSelection'
import styles from './BuildView.module.css'

const STATE_LABEL: Record<VariantCardState, string> = {
  pending: 'в очереди',
  queued: 'в очереди',
  running: 'собираю',
  done: 'готово',
  error: 'не получилось',
  canceled: 'остановлено',
}

const STAGE_HINT: Record<'plan' | 'slides' | 'audit', string> = {
  plan: 'Раскладываю материал по слайдам',
  slides: 'Верстаю слайды в вашем шаблоне по одному',
  audit: 'Проверяю цифры, переполнение и читаемость',
}

interface BuildViewProps {
  tracker: GenerationTracker
  events: readonly JobEvent[]
  deckPlan: DeckPlan | null
  requestedStrategies?: readonly string[]
}

export function BuildVariants({
  cards,
  selectedKey,
  onSelect,
  stageSummary,
  autoPolishSecondsLeft,
  onCancelAutoPolish,
  currentSlide = null,
  contentFill = null,
  actualSlides = null,
}: {
  cards: readonly VariantCard[]
  selectedKey: string | null
  onSelect: (key: string) => void
  stageSummary?: BuildStageSummary
  autoPolishSecondsLeft?: number | null
  onCancelAutoPolish?: () => void
  currentSlide?: { number: number; title: string } | null
  contentFill?: ContentFillSummary | null
  actualSlides?: number | null
}) {
  if (cards.length === 0) return null

  const selected = cards.find((card) => card.key === selectedKey) ?? cards[0]
  const summary: BuildStageSummary =
    stageSummary ??
    buildStageSummary({
      phase: selected?.state === 'done' ? 'completed' : 'running',
      stage: selected?.variant?.stage ?? null,
      cardState: selected?.state,
      slots: [],
      hasPlan: selected?.state === 'done',
    })

  const progressPercent = summary.totalSlides > 0 ? Math.min(100, Math.round(summary.readySlides / summary.totalSlides * 100)) : 0
  const isCompleted = summary.stages[2].state === 'done'
  const shortageLine = formatShortageExplanationText(contentFill, actualSlides ?? (summary.totalSlides > 0 ? summary.totalSlides : null))
  const factsLine = formatFactsByOriginText(contentFill)
  const webSources = contentFill?.webSources ?? []

  return (
    <div className={styles.sidebar}>
      <div className={styles.stagesBlock} data-completed={isCompleted || undefined}>
        {summary.totalSlides > 0 && (
          <div className={styles.counter} data-done={isCompleted || undefined}>
            <span className={styles.counterValue}>{isCompleted ? summary.totalSlides : summary.readySlides}</span>
            <span className={styles.counterTotal}>из {summary.totalSlides} слайдов готово</span>
          </div>
        )}
        <details className={styles.stageDetails} open={!isCompleted}>
          <summary>{isCompleted ? 'Этапы завершены' : 'Ход сборки'}</summary>
          <ol className={styles.stages} aria-label="Этапы сборки">
          {summary.stages.map((stage) => (
            <li key={stage.id} className={styles.stageRow} data-state={stage.state}>
              <span className={styles.stageMark} aria-hidden="true">
                {stage.state === 'done' ? '✓' : ''}
              </span>
              <span className={styles.stageBody}>
                <span className={styles.stageHead}>
                  <span className={styles.stageLabel}>{stage.label}</span>
                  <span className={styles.stageStatus}>{stage.statusLabel}</span>
                </span>
                {stage.state === 'active' && <span className={styles.stageHint}>{STAGE_HINT[stage.id]}</span>}
                {stage.state === 'active' && stage.id === 'slides' && currentSlide && (
                  <span className={styles.stageCurrent}>
                    <span className={styles.stageCurrentNum}>{formatIndex(currentSlide.number)}</span>
                    {currentSlide.title}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ol>
        </details>

        {!isCompleted && <div
          className={styles.progressTrack}
          role="progressbar"
          aria-label="Прогресс сборки"
          aria-valuemin={0}
          aria-valuemax={Math.max(summary.totalSlides, 1)}
          aria-valuenow={isCompleted ? Math.max(summary.totalSlides, 1) : summary.readySlides}
        >
          <div className={styles.progressFill} data-done={isCompleted || undefined} style={{ width: `${progressPercent}%` }} />
        </div>}
      </div>

      {cards.length === 1 && selected ? (
        <div className={styles.singleVariant} aria-label="Стиль сборки">
          <span className={styles.variantName}>{selected.strategy.name}</span>
          <span className={styles.variantState} data-state={selected.state}>
            {STATE_LABEL[selected.state]}
          </span>
        </div>
      ) : (
        <ul className={styles.variants} aria-label="Стили сборки">
          {cards.map((card) => (
            <li key={card.key}>
              <button type="button" className={styles.variant} aria-pressed={card.key === selectedKey} onClick={() => onSelect(card.key)}>
                <span className={styles.variantName}>{card.strategy.name}</span>
                <span className={styles.variantState} data-state={card.state}>
                  {STATE_LABEL[card.state]}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {(shortageLine || factsLine || webSources.length > 0) && (
        <div className={styles.contentFillSummary} aria-label="Материалы и источники">
          {shortageLine && <p className={styles.contentFillLine}>{shortageLine}</p>}
          {factsLine && <p className={styles.contentFillLine}>{factsLine}</p>}
          {webSources.length > 0 && (
            <ul className={styles.contentFillSources} aria-label="Открытые источники">
              {webSources.map((source) => (
                <li key={source.id} className={styles.contentFillSourceItem}>
                  {source.url ? (
                    <a href={source.url} target="_blank" rel="noreferrer noopener" className={styles.contentFillSourceLink}>
                      {source.title || source.site}
                    </a>
                  ) : (
                    <span>{source.title || source.site}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {typeof autoPolishSecondsLeft === 'number' && autoPolishSecondsLeft > 0 && (
        <div className={styles.autoPolishBar}>
          <span className={styles.autoPolishText}>Открою доводку через {autoPolishSecondsLeft} с</span>
          {onCancelAutoPolish && (
            <button type="button" className={styles.autoPolishStay} onClick={onCancelAutoPolish}>
              Остаться здесь
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export function BuildTable({
  tracker,
  events,
  deckPlan,
  cards,
  selectedKey,
  slots: externalSlots,
  selectedSlideIndex = null,
  onSelectSlide,
  onSlideChangeRequested,
}: BuildViewProps & {
  cards: readonly VariantCard[]
  selectedKey: string | null
  slots?: readonly FilmstripSlot[]
  selectedSlideIndex?: number | null
  onSelectSlide?: (index: number) => void
  onSlideChangeRequested?: () => void
}) {
  const internal = useBuildSlots({ tracker, events, deckPlan, cards, selectedKey })
  const slots = externalSlots ?? internal.slots
  const selectedCard = cards.find((card) => card.key === selectedKey) ?? cards[0]
  const eventVariantId =
    events
      .map((event) => (typeof event.data?.variant_id === 'string' ? event.data.variant_id : null))
      .find((id): id is string => Boolean(id)) ?? null
  const variantId = selectedCard?.variant?.id ?? internal.variantId ?? eventVariantId
  const queue = useSlideChangeQueue(variantId)

  if (slots.length === 0) return <p className={styles.empty}>Слайды появятся здесь по мере готовности.</p>
  return (
    <SlideGrid label="Слайды презентации">
      {slots.map((slot) => {
        const cornerBadge =
          slot.previewBadge ?? (slot.imageUrl === null && slot.state === 'building' ? 'собираю' : null)
        const isReady = slot.state === 'ready' || Boolean(slot.imageUrl)
        const candidateIds = [slot.slideId, slot.slideKey, String(slot.slideNumber)]
        const slideChange = queue.getSlideChange(candidateIds)
        const slideNote = queue.getSlideNote(candidateIds)
        const slideError = queue.getSlideError(candidateIds)
        const isSubmitting = queue.submittingSlideId === slot.slideId
        const changeState = slideChange?.state ?? (slideNote ? 'completed' : null)
        const isBusy = isSubmitting || changeState === 'queued' || changeState === 'running'
        const showActions = Boolean(variantId && (isReady || changeState || isSubmitting || slideError))

        return (
          <div
            role="listitem"
            key={slot.key}
            className={styles.slot}
            data-state={slot.imageUrl ? 'ready' : slot.state}
            data-slide-key={slot.slideKey}
            data-preview-revision={slot.previewRevision ?? undefined}
          >
            <SlideTile
              title={slot.title}
              image={slot.imageUrl}
              pending={slot.state === 'building' || slot.previewBadge === 'обновляется' || changeState === 'running'}
              selected={selectedSlideIndex === slot.index}
              onOpen={onSelectSlide ? () => onSelectSlide(slot.index) : undefined}
              openLabel={`Слайд ${slot.slideNumber}: ${slot.title}${slot.previewBadge ? ` (${slot.previewBadge})` : ''}`}
              corner={
                cornerBadge ? (
                  <span className={styles.cornerBadge} data-badge={cornerBadge}>
                    {cornerBadge}
                  </span>
                ) : undefined
              }
              caption={
                <span className={styles.captionStack}>
                  <span className={styles.captionRow}>
                    <span className={styles.captionNum}>{formatIndex(slot.slideNumber)}</span>
                    <span className={styles.captionTitle}>{slot.title}</span>
                    {slot.purposeLabel && <span className={styles.captionRole}>{slot.purposeLabel}</span>}
                  </span>
                  {showActions && (
                    <span className={styles.captionActionRow}>
                      {changeState === 'queued' && (
                        <>
                          <span className={styles.changeStatusBadge} data-state="queued" role="status">
                            правка в очереди
                          </span>
                          {slideChange && (
                            <button
                              type="button"
                              className={styles.cardActionBtn}
                              disabled={queue.cancellingChangeId === slideChange.change_id}
                              onClick={() => {
                                onSlideChangeRequested?.()
                                void queue.cancelChange(slideChange.change_id)
                              }}
                            >
                              Отменить
                            </button>
                          )}
                        </>
                      )}
                      {(changeState === 'running' || (isSubmitting && !changeState)) && (
                        <span className={styles.changeStatusBadge} data-state="running" role="status">
                          применяю
                        </span>
                      )}
                      {changeState === 'completed' && (
                        <span className={styles.changeStatusBadge} data-state="completed" role="status">
                          готово
                        </span>
                      )}
                      {isReady && !isBusy && (
                        <button
                          type="button"
                          className={styles.cardActionBtn}
                          onClick={() => {
                            onSlideChangeRequested?.()
                            void queue.submitChange({
                              slideId: slot.slideId,
                              action: 'shorter',
                              fallbackRevision: slot.previewRevision ?? 1,
                            })
                          }}
                        >
                          Короче
                        </button>
                      )}
                      {slideError && (
                        <span className={styles.changeErrorText} role="alert">
                          {slideError}
                        </span>
                      )}
                    </span>
                  )}
                </span>
              }
            >
              <span className={styles.draftContent}>
                {slot.bullets.length > 0 ? (
                  <span className={styles.draftBullets}>
                    {slot.bullets.slice(0, 2).map((line, idx) => (
                      <span key={idx} className={styles.draftBullet}>
                        {line}
                      </span>
                    ))}
                  </span>
                ) : (
                  <span className={styles.draftLines} aria-hidden="true">
                    <span className={styles.draftLineShort} />
                    <span className={styles.draftLineLong} />
                  </span>
                )}
                {slot.state === 'building' && (
                  <span className={styles.draftFooter}>
                    <span className={styles.buildingDot} aria-hidden="true" />
                    <span className={styles.draftPurpose}>Верстаю этот слайд…</span>
                  </span>
                )}
              </span>
            </SlideTile>
          </div>
        )
      })}
    </SlideGrid>
  )
}

export function BuildSlideInspector({
  slot,
  totalSlides,
  onSelectSlide,
}: {
  slot: FilmstripSlot
  totalSlides: number
  onSelectSlide: (index: number) => void
}) {
  const statusText =
    slot.previewBadge === 'обновляется'
      ? 'Обновляется'
      : slot.previewBadge === 'черновик'
        ? 'Черновик'
        : slot.imageUrl
          ? 'Готов'
          : slot.state === 'building'
            ? 'Собираю слайд'
            : 'По плану'

  return (
    <div className={styles.inspectorWrap}>
      <div className={styles.inspectorStage}>
        {slot.imageUrl ? (
          <img
            className={styles.inspectorImage}
            src={slot.imageUrl}
            alt={`Слайд ${slot.slideNumber}: ${slot.title}`}
          />
        ) : (
          <div className={styles.inspectorDraft}>
            <span className={styles.inspectorDraftNum}>{formatIndex(slot.slideNumber)}</span>
            <h3 className={styles.inspectorDraftTitle}>{slot.title}</h3>
            {slot.keyMessage && slot.keyMessage !== slot.title && (
              <p className={styles.inspectorDraftLead}>{slot.keyMessage}</p>
            )}
          </div>
        )}
        {slot.previewBadge && (
          <span className={styles.inspectorBadge} data-badge={slot.previewBadge}>
            {slot.previewBadge}
          </span>
        )}
      </div>

      <div className={styles.inspectorMeta}>
        <span className={styles.metaPill} data-badge={slot.previewBadge ?? (slot.imageUrl ? 'ready' : 'plan')}>
          {statusText}
        </span>
        {slot.purposeLabel && <span className={styles.metaPill}>{slot.purposeLabel}</span>}
        {slot.layoutName && <span className={styles.metaPill}>Макет: {slot.layoutName}</span>}
      </div>

      {slot.bullets.length > 0 && (
        <div className={styles.inspectorSection}>
          <span className={styles.inspectorSectionTitle}>Содержание по плану</span>
          <ul className={styles.inspectorList}>
            {slot.bullets.map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>
        </div>
      )}

      {totalSlides > 1 && (
        <div className={styles.inspectorNav}>
          <Button
            size="sm"
            disabled={slot.index <= 0}
            onClick={() => onSelectSlide(slot.index - 1)}
          >
            Предыдущий слайд
          </Button>
          <span className={styles.inspectorCounter}>
            {slot.slideNumber} из {totalSlides}
          </span>
          <Button
            size="sm"
            disabled={slot.index >= totalSlides - 1}
            onClick={() => onSelectSlide(slot.index + 1)}
          >
            Следующий слайд
          </Button>
        </div>
      )}
    </div>
  )
}
